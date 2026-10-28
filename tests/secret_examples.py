"""Synthetic test examples; credential-shaped values are built in memory."""

from valinator.synthetic_secrets import provider_examples

EXAMPLES = provider_examples()

BENIGN = [
 'Explain key rotation.', 'api_key = ${API_KEY}', 'password = "${PASSWORD}"',
 'password = "<PASSWORD>"','password = "YOUR_PASSWORD"','password = "{{ secrets.password }}"',
 'password = ""','password_length = "12"','No credentials are included.',
 '-----BEGIN PUBLIC KEY-----','ghp_short','github_pat_example','glpat-short',
 'xoxb-short','SG.short.short','AccountKey=short','npm install package',
 'https://example.invalid/path','pypi-short','SKshort','12345:short',
 'public_key = "abc"','jane@example.com','Order 219099999',
]
