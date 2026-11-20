"""Provider failures, cursor forgery, leakage and bounds fail closed."""
from datetime import datetime, timezone
import pytest
from guardoath.admin_users import CognitoUserDirectory, InvalidUserCursor

class Fake:
    def __init__(self): self.calls=[]
    def list_users(self, **kw):
        self.calls.append(kw)
        if "PaginationToken" in kw: return {"Users":[]}
        return {"Users":[{"Username":"test-subject", "Enabled":True, "UserStatus":"CONFIRMED",
                         "UserCreateDate":datetime(2026,11,1,tzinfo=timezone.utc),
                         "Attributes":[{"Name":"email","Value":"someone@example.com"},
                                       {"Name":"email_verified","Value":"true"},
                                       {"Name":"custom:tenant_id","Value":"private-tenant"},
                                       {"Name":"custom:token","Value":"private-token"}]}],
                "PaginationToken":"next-provider"}

def directory(fake=None, clock=lambda: 100):
    return CognitoUserDirectory(fake or Fake(), "us-east-1_Example", b"a"*32, clock)

def test_list_is_one_bounded_page_masked_and_paginates():
    f=Fake(); d=directory(f); first=d.page(limit=5,query="someone@")
    assert len(f.calls)==1 and f.calls[0]=={"UserPoolId":"us-east-1_Example","Limit":5,"Filter":'email ^= "someone@"'}
    assert first["users"][0] == {"username":"test-subject","email_hint":"s***@example.com",
                                 "email_verified":True,"enabled":True,"status":"CONFIRMED",
                                 "created_utc":"2026-11-01T00:00:00Z","updated_utc":None}
    assert first["total"] is None and first["next_cursor"]
    assert "someone@example.com" not in str(first) and "private-tenant" not in str(first)
    after=d.page(limit=5,query="someone@",cursor=first["next_cursor"])
    assert after["users"]==[] and after["next_cursor"] is None
    assert f.calls[-1]["PaginationToken"]=="next-provider"

@pytest.mark.parametrize("query",['broken"',"bad\\","é"," a","a"*81])
def test_reject_unsafe_filters_before_provider(query):
    f=Fake()
    with pytest.raises(ValueError): directory(f).page(query=query)
    assert f.calls==[]

@pytest.mark.parametrize("size",[0,51,True,1.5])
def test_reject_invalid_page_sizes(size):
    f=Fake()
    with pytest.raises(ValueError): directory(f).page(limit=size)
    assert f.calls==[]

def test_invalid_expired_or_changed_cursor_never_reaches_provider():
    f=Fake(); d=directory(f); c=d.page(limit=2,query="a")["next_cursor"]
    for cur,query,limit in [(c+"x","a",2),(c,"b",2),(c,"a",3),("é","a",2)]:
        with pytest.raises(InvalidUserCursor): d.page(cursor=cur,query=query,limit=limit)
    with pytest.raises(InvalidUserCursor): directory(f,clock=lambda:2000).page(cursor=c,query="a",limit=2)
    assert len(f.calls)==1

def test_provider_oversize_or_unexpected_attributes_fail_closed():
    class Oversize(Fake):
        def list_users(self,**kw): return {"Users":[{"Username":"x"}]*51}
    with pytest.raises(ValueError): directory(Oversize()).page()

def test_missing_provider_or_weak_key_is_rejected():
    with pytest.raises(ValueError): CognitoUserDirectory(object(),"us-east-1_Example",b"a"*32)
    with pytest.raises(ValueError): CognitoUserDirectory(Fake(),"us-east-1_Example",b"short")


def test_cognito_email_username_is_masked():
    class EmailUsername(Fake):
        def list_users(self, **kw):
            return {"Users": [{"Username": "person@example.com",
                               "Attributes": [{"Name": "email", "Value": "person@example.com"}]}]}
    result = directory(EmailUsername()).page()
    assert result["users"][0]["username"] == "p***@example.com"
    assert "person@example.com" not in str(result)
