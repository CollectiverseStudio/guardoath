# Privacy group regression examples

100 expectations; 12 intentionally require an incomplete result. These are synthetic examples, not patient records or a measured accuracy benchmark. Each row checks exactly the finding set, checker/evaluation status, recommendation, effective decision and redaction status stored in the fixture.

| Case | Example / use case | Expected result |
| --- | --- | --- |
| `email.input` | `Email jane@example.com` | complete; pii.email_address; recommend flag / effective allow |
| `email.output` | `Email jane@example.com` | complete; pii.email_address; recommend flag / effective allow |
| `card.input` | `Card 4111 1111 1111 1111` | complete; pii.credit_card; recommend flag / effective allow |
| `card.output` | `Card 4111 1111 1111 1111` | complete; pii.credit_card; recommend flag / effective allow |
| `ssn.input` | `SSN: 219-09-9999` | complete; pii.us_ssn; recommend flag / effective allow |
| `ssn.output` | `SSN: 219-09-9999` | complete; pii.us_ssn; recommend flag / effective allow |
| `phone_number0.input` | `Phone: 202-555-0147` | complete; pii.phone_number; recommend flag / effective allow |
| `phone_number0.output` | `Phone: 202-555-0147` | complete; pii.phone_number; recommend flag / effective allow |
| `phone_number1.input` | `telephone = +44 20 7946 0958` | complete; pii.phone_number; recommend flag / effective allow |
| `phone_number1.output` | `telephone = +44 20 7946 0958` | complete; pii.phone_number; recommend flag / effective allow |
| `phone_number2.input` | `phone_number: "(202) 555-0147"` | complete; pii.phone_number; recommend block / effective block |
| `phone_number2.output` | `phone_number: "(202) 555-0147"` | complete; pii.phone_number; recommend block / effective block |
| `date_of_birth0.input` | `DOB: 1980-02-29` | complete; pii.date_of_birth; recommend flag / effective allow |
| `date_of_birth0.output` | `DOB: 1980-02-29` | complete; pii.date_of_birth; recommend flag / effective allow |
| `date_of_birth1.input` | `date of birth = 12/31/1990` | complete; pii.date_of_birth; recommend flag / effective allow |
| `date_of_birth1.output` | `date of birth = 12/31/1990` | complete; pii.date_of_birth; recommend flag / effective allow |
| `date_of_birth2.input` | `birth-date: "2000-01-01"` | complete; pii.date_of_birth; recommend block / effective block |
| `date_of_birth2.output` | `birth-date: "2000-01-01"` | complete; pii.date_of_birth; recommend block / effective block |
| `medical_record_number0.input` | `MRN: MRN-001234` | complete; pii.medical_record_number; recommend flag / effective allow |
| `medical_record_number0.output` | `MRN: MRN-001234` | complete; pii.medical_record_number; recommend flag / effective allow |
| `medical_record_number1.input` | `medical record number = A1234` | complete; pii.medical_record_number; recommend flag / effective allow |
| `medical_record_number1.output` | `medical record number = A1234` | complete; pii.medical_record_number; recommend flag / effective allow |
| `medical_record_number2.input` | `mrn: "987654"` | complete; pii.medical_record_number; recommend block / effective block |
| `medical_record_number2.output` | `mrn: "987654"` | complete; pii.medical_record_number; recommend block / effective block |
| `health_plan_id0.input` | `member_id: PLAN-12345` | complete; pii.health_plan_id; recommend flag / effective allow |
| `health_plan_id0.output` | `member_id: PLAN-12345` | complete; pii.health_plan_id; recommend flag / effective allow |
| `health_plan_id1.input` | `health plan id = ABC1234` | complete; pii.health_plan_id; recommend flag / effective allow |
| `health_plan_id1.output` | `health plan id = ABC1234` | complete; pii.health_plan_id; recommend flag / effective allow |
| `health_plan_id2.input` | `insurance-member-id: "M000123"` | complete; pii.health_plan_id; recommend block / effective block |
| `health_plan_id2.output` | `insurance-member-id: "M000123"` | complete; pii.health_plan_id; recommend block / effective block |
| `bank_account0.input` | `bank account: 12345678` | complete; pii.bank_account; recommend flag / effective allow |
| `bank_account0.output` | `bank account: 12345678` | complete; pii.bank_account; recommend flag / effective allow |
| `bank_account1.input` | `bank_account_number = 123456789012` | complete; pii.bank_account; recommend flag / effective allow |
| `bank_account1.output` | `bank_account_number = 123456789012` | complete; pii.bank_account; recommend flag / effective allow |
| `bank_account2.input` | `account-number: "76543210"` | complete; pii.bank_account; recommend block / effective block |
| `bank_account2.output` | `account-number: "76543210"` | complete; pii.bank_account; recommend block / effective block |
| `lookalike0.input` | `Order reference 202-555-0147` | complete; passed; recommend allow / effective allow |
| `lookalike0.output` | `Order reference 202-555-0147` | complete; passed; recommend allow / effective allow |
| `lookalike1.input` | `Calendar date 1980-02-29` | complete; passed; recommend allow / effective allow |
| `lookalike1.output` | `Calendar date 1980-02-29` | complete; passed; recommend allow / effective allow |
| `lookalike2.input` | `Inventory MRN-001234` | complete; passed; recommend allow / effective allow |
| `lookalike2.output` | `Inventory MRN-001234` | complete; passed; recommend allow / effective allow |
| `lookalike3.input` | `Plan ABC1234` | complete; passed; recommend allow / effective allow |
| `lookalike3.output` | `Plan ABC1234` | complete; passed; recommend allow / effective allow |
| `lookalike4.input` | `Reference 76543210` | complete; passed; recommend allow / effective allow |
| `lookalike4.output` | `Reference 76543210` | complete; passed; recommend allow / effective allow |
| `lookalike5.input` | `Phone: 123` | complete; passed; recommend allow / effective allow |
| `lookalike5.output` | `Phone: 123` | complete; passed; recommend allow / effective allow |
| `lookalike6.input` | `DOB: 2025-02-29` | complete; passed; recommend allow / effective allow |
| `lookalike6.output` | `DOB: 2025-02-29` | complete; passed; recommend allow / effective allow |
| `lookalike7.input` | `MRN: ABC` | complete; passed; recommend allow / effective allow |
| `lookalike7.output` | `MRN: ABC` | complete; passed; recommend allow / effective allow |
| `lookalike8.input` | `member_id: ABCD` | complete; passed; recommend allow / effective allow |
| `lookalike8.output` | `member_id: ABCD` | complete; passed; recommend allow / effective allow |
| `lookalike9.input` | `bank account: ABC12345` | complete; passed; recommend allow / effective allow |
| `lookalike9.output` | `bank account: ABC12345` | complete; passed; recommend allow / effective allow |
| `lookalike10.input` | `customer_phone_typo: 202-555-0147` | complete; passed; recommend allow / effective allow |
| `lookalike10.output` | `customer_phone_typo: 202-555-0147` | complete; passed; recommend allow / effective allow |
| `lookalike11.input` | `Please explain what an MRN means.` | complete; passed; recommend allow / effective allow |
| `lookalike11.output` | `Please explain what an MRN means.` | complete; passed; recommend allow / effective allow |
| `lookalike12.input` | `DOB: [REDACTED_DATE_OF_BIRTH]` | complete; passed; recommend allow / effective allow |
| `lookalike12.output` | `DOB: [REDACTED_DATE_OF_BIRTH]` | complete; passed; recommend allow / effective allow |
| `lookalike13.input` | `MRN:` | complete; passed; recommend allow / effective allow |
| `lookalike13.output` | `MRN:` | complete; passed; recommend allow / effective allow |
| `lookalike14.input` | `name: Synthetic Person` | complete; passed; recommend allow / effective allow |
| `lookalike14.output` | `name: Synthetic Person` | complete; passed; recommend allow / effective allow |
| `lookalike15.input` | `address: 123 Example Street` | complete; passed; recommend allow / effective allow |
| `lookalike15.output` | `address: 123 Example Street` | complete; passed; recommend allow / effective allow |
| `json.phone_number.input` | `{"phone_number":["202-555-0147"]}` | complete; pii.phone_number; recommend flag / effective allow |
| `json.phone_number.output` | `{"phone_number":["202-555-0147"]}` | complete; pii.phone_number; recommend flag / effective allow |
| `json.date_of_birth.input` | `{"nested":{"date_of_birth":"1980\u002d02-29"}}` | complete; pii.date_of_birth; recommend flag / effective allow |
| `json.date_of_birth.output` | `{"nested":{"date_of_birth":"1980\u002d02-29"}}` | complete; pii.date_of_birth; recommend flag / effective allow |
| `json.medical_record_number.input` | `{"MRN":987654}` | complete; pii.medical_record_number; recommend flag / effective allow |
| `json.medical_record_number.output` | `{"MRN":987654}` | complete; pii.medical_record_number; recommend flag / effective allow |
| `json.health_plan_id.input` | `{"insurance_member_id":"M000123"}` | complete; pii.health_plan_id; recommend flag / effective allow |
| `json.health_plan_id.output` | `{"insurance_member_id":"M000123"}` | complete; pii.health_plan_id; recommend flag / effective allow |
| `json.bank_account.input` | `{"bank_account_number":76543210}` | complete; pii.bank_account; recommend flag / effective allow |
| `json.bank_account.output` | `{"bank_account_number":76543210}` | complete; pii.bank_account; recommend flag / effective allow |
| `redact.observe.text.input` | `Phone: 202-555-0147 / DOB: 1980-02-29 / MRN: MRN-001234 / member_id: PLAN-12345 / bank account: 12345678` | complete; pii.phone_number, pii.date_of_birth, pii.medical_record_number, pii.health_plan_id, pii.bank_account; recommend redact / effective allow; redaction preview |
| `redact.observe.text.output` | `Phone: 202-555-0147 / DOB: 1980-02-29 / MRN: MRN-001234 / member_id: PLAN-12345 / bank account: 12345678` | complete; pii.phone_number, pii.date_of_birth, pii.medical_record_number, pii.health_plan_id, pii.bank_account; recommend redact / effective allow; redaction preview |
| `redact.observe.json.input` | `{"phone_number": 2025550147, "DOB": "1980-02-29", "MRN": "A1234", "member_id": "M000123", "bank_account_number": 76543210}` | complete; pii.phone_number, pii.date_of_birth, pii.medical_record_number, pii.health_plan_id, pii.bank_account; recommend redact / effective allow; redaction preview |
| `redact.observe.json.output` | `{"phone_number": 2025550147, "DOB": "1980-02-29", "MRN": "A1234", "member_id": "M000123", "bank_account_number": 76543210}` | complete; pii.phone_number, pii.date_of_birth, pii.medical_record_number, pii.health_plan_id, pii.bank_account; recommend redact / effective allow; redaction preview |
| `redact.enforce.text.input` | `Phone: 202-555-0147 / DOB: 1980-02-29 / MRN: MRN-001234 / member_id: PLAN-12345 / bank account: 12345678` | complete; pii.phone_number, pii.date_of_birth, pii.medical_record_number, pii.health_plan_id, pii.bank_account; recommend redact / effective redact; redaction ready |
| `redact.enforce.text.output` | `Phone: 202-555-0147 / DOB: 1980-02-29 / MRN: MRN-001234 / member_id: PLAN-12345 / bank account: 12345678` | complete; pii.phone_number, pii.date_of_birth, pii.medical_record_number, pii.health_plan_id, pii.bank_account; recommend redact / effective redact; redaction ready |
| `redact.enforce.json.input` | `{"phone_number": 2025550147, "DOB": "1980-02-29", "MRN": "A1234", "member_id": "M000123", "bank_account_number": 76543210}` | complete; pii.phone_number, pii.date_of_birth, pii.medical_record_number, pii.health_plan_id, pii.bank_account; recommend redact / effective redact; redaction ready |
| `redact.enforce.json.output` | `{"phone_number": 2025550147, "DOB": "1980-02-29", "MRN": "A1234", "member_id": "M000123", "bank_account_number": 76543210}` | complete; pii.phone_number, pii.date_of_birth, pii.medical_record_number, pii.health_plan_id, pii.bank_account; recommend redact / effective redact; redaction ready |
| `invalid0.input` | `not json` | incomplete; error; recommend block / effective allow |
| `invalid0.output` | `not json` | incomplete; error; recommend block / effective allow |
| `invalid1.input` | `{"x":1,"x":2}` | incomplete; error; recommend block / effective allow |
| `invalid1.output` | `{"x":1,"x":2}` | incomplete; error; recommend block / effective allow |
| `invalid2.input` | `{"x":NaN}` | incomplete; error; recommend block / effective allow |
| `invalid2.output` | `{"x":NaN}` | incomplete; error; recommend block / effective allow |
| `invalid3.input` | `{"x":1e400}` | incomplete; error; recommend block / effective allow |
| `invalid3.output` | `{"x":1e400}` | incomplete; error; recommend block / effective allow |
| `field.length.limit.input` | `MRN value of 129 characters (field length budget)` | incomplete; error; recommend block / effective allow |
| `field.length.limit.output` | `MRN value of 129 characters (field length budget)` | incomplete; error; recommend block / effective allow |
| `field.count.limit.input` | `65 repeated MRN fields (document budget)` | incomplete; error; recommend block / effective allow |
| `field.count.limit.output` | `65 repeated MRN fields (document budget)` | incomplete; error; recommend block / effective allow |
| `disabled.by.default.input` | `Phone: 202-555-0147` | complete; passed; recommend allow / effective allow |
| `disabled.by.default.output` | `Phone: 202-555-0147` | complete; passed; recommend allow / effective allow |
