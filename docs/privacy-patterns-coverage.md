# Privacy pattern regression examples

100 expectations (50 scenarios on the input and output stages); none is intentionally incomplete.
These are synthetic values from the copied upstream tests, not real identifiers or a measured
accuracy benchmark. Each row checks exactly the finding set, checker and evaluation status, both
recommended and effective actions, and the redaction status. Profiles are in
`policies/privacy-patterns-batch.json`.

| Case | Profile | Example / use case | Expected result |
| --- | --- | --- | --- |
| `financial.iban.input` | `privacy-patterns-v1` | `Please send the refund to IBAN DE89 3704 0044 0532 0130 00.` | complete; pii.iban_code; recommend flag / effective allow |
| `financial.iban.output` | `privacy-patterns-v1` | `Please send the refund to IBAN DE89 3704 0044 0532 0130 00.` | complete; pii.iban_code; recommend flag / effective allow |
| `financial.crypto.input` | `privacy-patterns-v1` | `My bitcoin wallet is 16Yeky6GMjeNkAiNcBY7ZhrLoMSgg1BoyZ.` | complete; pii.crypto; recommend flag / effective allow |
| `financial.crypto.output` | `privacy-patterns-v1` | `My bitcoin wallet is 16Yeky6GMjeNkAiNcBY7ZhrLoMSgg1BoyZ.` | complete; pii.crypto; recommend flag / effective allow |
| `financial.us-bank.input` | `privacy-patterns-v1` | `My checking account is 945456787654.` | complete; pii.us_bank_number; recommend flag / effective allow |
| `financial.us-bank.output` | `privacy-patterns-v1` | `My checking account is 945456787654.` | complete; pii.us_bank_number; recommend flag / effective allow |
| `financial.aba-routing.input` | `privacy-patterns-v1` | `Routing number 121000358 for the transfer.` | complete; pii.aba_routing_number; recommend flag / effective allow |
| `financial.aba-routing.output` | `privacy-patterns-v1` | `Routing number 121000358 for the transfer.` | complete; pii.aba_routing_number; recommend flag / effective allow |
| `network.ip.input` | `privacy-patterns-v1` | `The server is at 192.168.1.10 today.` | complete; pii.ip_address; recommend flag / effective allow |
| `network.ip.output` | `privacy-patterns-v1` | `The server is at 192.168.1.10 today.` | complete; pii.ip_address; recommend flag / effective allow |
| `network.mac.input` | `privacy-patterns-v1` | `Device MAC: 01:23:45:67:89:AB` | complete; pii.mac_address; recommend flag / effective allow |
| `network.mac.output` | `privacy-patterns-v1` | `Device MAC: 01:23:45:67:89:AB` | complete; pii.mac_address; recommend flag / effective allow |
| `network.url.input` | `privacy-patterns-v1` | `Read more at https://example.com/help` | complete; pii.url; recommend flag / effective allow |
| `network.url.output` | `privacy-patterns-v1` | `Read more at https://example.com/help` | complete; pii.url; recommend flag / effective allow |
| `network.uuid.input` | `privacy-patterns-v1` | `Ticket 6ba7b810-9dad-31d1-80b4-00c04fd430c8 is open.` | complete; pii.uuid; recommend flag / effective allow |
| `network.uuid.output` | `privacy-patterns-v1` | `Ticket 6ba7b810-9dad-31d1-80b4-00c04fd430c8 is open.` | complete; pii.uuid; recommend flag / effective allow |
| `date.date.input` | `privacy-patterns-v1` | `Your appointment is on 5-20-2021.` | complete; pii.date_time; recommend flag / effective allow |
| `date.date.output` | `privacy-patterns-v1` | `Your appointment is on 5-20-2021.` | complete; pii.date_time; recommend flag / effective allow |
| `phone.generic.input` | `privacy-patterns-v1` | `Call me on (415) 555-0132.` | complete; pii.phone_number; recommend flag / effective allow |
| `phone.generic.output` | `privacy-patterns-v1` | `Call me on (415) 555-0132.` | complete; pii.phone_number; recommend flag / effective allow |
| `phone.za-mobile.input` | `privacy-patterns-v1` | `My cell is +27632118258` | complete; pii.phone_number, pii.za_mobile_number; recommend flag / effective allow |
| `phone.za-mobile.output` | `privacy-patterns-v1` | `My cell is +27632118258` | complete; pii.phone_number, pii.za_mobile_number; recommend flag / effective allow |
| `us.passport.input` | `privacy-patterns-v1` | `My passport number is 912803456.` | complete; pii.us_passport; recommend flag / effective allow |
| `us.passport.output` | `privacy-patterns-v1` | `My passport number is 912803456.` | complete; pii.us_passport; recommend flag / effective allow |
| `us.itin.input` | `privacy-patterns-v1` | `My ITIN is 911-70-1234.` | complete; pii.us_itin; recommend flag / effective allow |
| `us.itin.output` | `privacy-patterns-v1` | `My ITIN is 911-70-1234.` | complete; pii.us_itin; recommend flag / effective allow |
| `us.driver-license.input` | `privacy-patterns-v1` | `My driver license is H12234567.` | complete; pii.us_driver_license; recommend flag / effective allow |
| `us.driver-license.output` | `privacy-patterns-v1` | `My driver license is H12234567.` | complete; pii.us_driver_license; recommend flag / effective allow |
| `us.mbi.input` | `privacy-patterns-v1` | `Medicare MBI 1EG4-TE5-MK73` | complete; pii.us_mbi; recommend flag / effective allow |
| `us.mbi.output` | `privacy-patterns-v1` | `Medicare MBI 1EG4-TE5-MK73` | complete; pii.us_mbi; recommend flag / effective allow |
| `us.health-member-id.input` | `privacy-patterns-v1` | `Member ID A12345` | complete; pii.us_health_insurance_member_id; recommend flag / effective allow |
| `us.health-member-id.output` | `privacy-patterns-v1` | `Member ID A12345` | complete; pii.us_health_insurance_member_id; recommend flag / effective allow |
| `uk.nino.input` | `privacy-patterns-v1` | `National insurance number AB123456C` | complete; pii.uk_nino, pii.us_health_insurance_member_id; recommend flag / effective allow |
| `uk.nino.output` | `privacy-patterns-v1` | `National insurance number AB123456C` | complete; pii.uk_nino, pii.us_health_insurance_member_id; recommend flag / effective allow |
| `uk.nhs.input` | `privacy-patterns-v1` | `NHS number 401-023-2137` | complete; pii.uk_nhs; recommend flag / effective allow |
| `uk.nhs.output` | `privacy-patterns-v1` | `NHS number 401-023-2137` | complete; pii.uk_nhs; recommend flag / effective allow |
| `uk.postcode.input` | `privacy-patterns-v1` | `My address is SW1A 1AA in London` | complete; pii.uk_postcode; recommend flag / effective allow |
| `uk.postcode.output` | `privacy-patterns-v1` | `My address is SW1A 1AA in London` | complete; pii.uk_postcode; recommend flag / effective allow |
| `eu.de-tax-id.input` | `privacy-patterns-v1` | `Steuer-ID 98765432106` | complete; pii.de_tax_id; recommend flag / effective allow |
| `eu.de-tax-id.output` | `privacy-patterns-v1` | `Steuer-ID 98765432106` | complete; pii.de_tax_id; recommend flag / effective allow |
| `eu.es-nif.input` | `privacy-patterns-v1` | `Mi NIF es 1111111G` | complete; pii.es_nif; recommend flag / effective allow |
| `eu.es-nif.output` | `privacy-patterns-v1` | `Mi NIF es 1111111G` | complete; pii.es_nif; recommend flag / effective allow |
| `eu.it-fiscal-code.input` | `privacy-patterns-v1` | `Codice fiscale AAAAAA00B11C333Y` | complete; pii.it_fiscal_code; recommend flag / effective allow |
| `eu.it-fiscal-code.output` | `privacy-patterns-v1` | `Codice fiscale AAAAAA00B11C333Y` | complete; pii.it_fiscal_code; recommend flag / effective allow |
| `eu.pl-pesel.input` | `privacy-patterns-v1` | `PESEL 44051401458` | complete; pii.pl_pesel; recommend flag / effective allow |
| `eu.pl-pesel.output` | `privacy-patterns-v1` | `PESEL 44051401458` | complete; pii.pl_pesel; recommend flag / effective allow |
| `eu.se-personnummer.input` | `privacy-patterns-v1` | `Personnummer 20110925-2385` | complete; pii.se_personnummer; recommend flag / effective allow |
| `eu.se-personnummer.output` | `privacy-patterns-v1` | `Personnummer 20110925-2385` | complete; pii.se_personnummer; recommend flag / effective allow |
| `apac.au-tfn.input` | `privacy-patterns-v1` | `TFN 876543210` | complete; pii.au_tfn; recommend flag / effective allow |
| `apac.au-tfn.output` | `privacy-patterns-v1` | `TFN 876543210` | complete; pii.au_tfn; recommend flag / effective allow |
| `apac.in-aadhaar.input` | `privacy-patterns-v1` | `Aadhaar 312345678909` | complete; pii.in_aadhaar; recommend flag / effective allow |
| `apac.in-aadhaar.output` | `privacy-patterns-v1` | `Aadhaar 312345678909` | complete; pii.in_aadhaar; recommend flag / effective allow |
| `apac.kr-rrn.input` | `privacy-patterns-v1` | `RRN 960121-1234567` | complete; pii.kr_rrn; recommend flag / effective allow |
| `apac.kr-rrn.output` | `privacy-patterns-v1` | `RRN 960121-1234567` | complete; pii.kr_rrn; recommend flag / effective allow |
| `apac.sg-nric.input` | `privacy-patterns-v1` | `NRIC S2740116C` | complete; pii.sg_nric_fin; recommend flag / effective allow |
| `apac.sg-nric.output` | `privacy-patterns-v1` | `NRIC S2740116C` | complete; pii.sg_nric_fin; recommend flag / effective allow |
| `africa.za-id.input` | `privacy-patterns-v1` | `ID number 8001015009087` | complete; pii.kr_frn, pii.za_id_number; recommend flag / effective allow |
| `africa.za-id.output` | `privacy-patterns-v1` | `ID number 8001015009087` | complete; pii.kr_frn, pii.za_id_number; recommend flag / effective allow |
| `africa.ng-nin.input` | `privacy-patterns-v1` | `Nigerian NIN 23456789019` | complete; pii.ng_nin; recommend flag / effective allow |
| `africa.ng-nin.output` | `privacy-patterns-v1` | `Nigerian NIN 23456789019` | complete; pii.ng_nin; recommend flag / effective allow |
| `americas.ca-sin.input` | `privacy-patterns-v1` | `SIN 130 692 544` | complete; pii.ca_sin; recommend flag / effective allow |
| `americas.ca-sin.output` | `privacy-patterns-v1` | `SIN 130 692 544` | complete; pii.ca_sin; recommend flag / effective allow |
| `json.field-context.input` | `privacy-patterns-v1` | `{"passport_number": "912803456"}` | complete; pii.us_passport; recommend flag / effective allow |
| `json.field-context.output` | `privacy-patterns-v1` | `{"passport_number": "912803456"}` | complete; pii.us_passport; recommend flag / effective allow |
| `failure.iban-checksum.input` | `privacy-patterns-v1` | `IBAN DE89 3704 0044 0532 0130 01` | complete; passed; recommend allow / effective allow |
| `failure.iban-checksum.output` | `privacy-patterns-v1` | `IBAN DE89 3704 0044 0532 0130 01` | complete; passed; recommend allow / effective allow |
| `failure.nhs-checksum.input` | `privacy-patterns-v1` | `NHS number 401-023-2138` | complete; passed; recommend allow / effective allow |
| `failure.nhs-checksum.output` | `privacy-patterns-v1` | `NHS number 401-023-2138` | complete; passed; recommend allow / effective allow |
| `benign.bare-number.input` | `privacy-patterns-v1` | `Order 912803456 shipped.` | complete; passed; recommend allow / effective allow |
| `benign.bare-number.output` | `privacy-patterns-v1` | `Order 912803456 shipped.` | complete; passed; recommend allow / effective allow |
| `benign.clean-question.input` | `privacy-patterns-v1` | `Can you explain your return policy?` | complete; passed; recommend allow / effective allow |
| `benign.clean-question.output` | `privacy-patterns-v1` | `Can you explain your return policy?` | complete; passed; recommend allow / effective allow |
| `benign.version.input` | `privacy-patterns-v1` | `Version 3.14.10 is installed.` | complete; passed; recommend allow / effective allow |
| `benign.version.output` | `privacy-patterns-v1` | `Version 3.14.10 is installed.` | complete; passed; recommend allow / effective allow |
| `config.off-iban.input` | `privacy-patterns-off-v1` | `IBAN DE89 3704 0044 0532 0130 00` | complete; passed; recommend allow / effective allow |
| `config.off-iban.output` | `privacy-patterns-off-v1` | `IBAN DE89 3704 0044 0532 0130 00` | complete; passed; recommend allow / effective allow |
| `config.off-keeps-core.input` | `privacy-patterns-off-v1` | `Card 4111 1111 1111 1111` | complete; pii.credit_card; recommend flag / effective allow |
| `config.off-keeps-core.output` | `privacy-patterns-off-v1` | `Card 4111 1111 1111 1111` | complete; pii.credit_card; recommend flag / effective allow |
| `config.disabled-iban.input` | `privacy-patterns-no-iban-v1` | `IBAN DE89 3704 0044 0532 0130 00` | complete; passed; recommend allow / effective allow |
| `config.disabled-iban.output` | `privacy-patterns-no-iban-v1` | `IBAN DE89 3704 0044 0532 0130 00` | complete; passed; recommend allow / effective allow |
| `config.disabled-keeps-others.input` | `privacy-patterns-no-iban-v1` | `My bitcoin wallet is 16Yeky6GMjeNkAiNcBY7ZhrLoMSgg1BoyZ.` | complete; pii.crypto; recommend flag / effective allow |
| `config.disabled-keeps-others.output` | `privacy-patterns-no-iban-v1` | `My bitcoin wallet is 16Yeky6GMjeNkAiNcBY7ZhrLoMSgg1BoyZ.` | complete; pii.crypto; recommend flag / effective allow |
| `config.strict-context-only.input` | `privacy-patterns-strict-v1` | `My passport number is 912803456.` | complete; passed; recommend allow / effective allow |
| `config.strict-context-only.output` | `privacy-patterns-strict-v1` | `My passport number is 912803456.` | complete; passed; recommend allow / effective allow |
| `config.strict-validated.input` | `privacy-patterns-strict-v1` | `IBAN DE89 3704 0044 0532 0130 00` | complete; pii.iban_code; recommend flag / effective allow |
| `config.strict-validated.output` | `privacy-patterns-strict-v1` | `IBAN DE89 3704 0044 0532 0130 00` | complete; pii.iban_code; recommend flag / effective allow |
| `config.low-threshold.input` | `privacy-patterns-low-threshold-v1` | `Order 912803456 shipped.` | complete; pii.de_bsnr, pii.us_bank_number, pii.us_itin, pii.us_passport; recommend flag / effective allow |
| `config.low-threshold.output` | `privacy-patterns-low-threshold-v1` | `Order 912803456 shipped.` | complete; pii.de_bsnr, pii.us_bank_number, pii.us_itin, pii.us_passport; recommend flag / effective allow |
| `enforce.iban.input` | `privacy-patterns-enforce-v1` | `Please send the refund to IBAN DE89 3704 0044 0532 0130 00.` | complete; pii.iban_code; recommend block / effective block |
| `enforce.iban.output` | `privacy-patterns-enforce-v1` | `Please send the refund to IBAN DE89 3704 0044 0532 0130 00.` | complete; pii.iban_code; recommend block / effective block |
| `enforce.clean.input` | `privacy-patterns-enforce-v1` | `Can you explain your return policy?` | complete; passed; recommend allow / effective allow |
| `enforce.clean.output` | `privacy-patterns-enforce-v1` | `Can you explain your return policy?` | complete; passed; recommend allow / effective allow |
| `redact.iban.input` | `privacy-patterns-redact-v1` | `Send the refund to IBAN DE89 3704 0044 0532 0130 00 please.` | complete; pii.iban_code; recommend redact / effective allow; redaction preview |
| `redact.iban.output` | `privacy-patterns-redact-v1` | `Send the refund to IBAN DE89 3704 0044 0532 0130 00 please.` | complete; pii.iban_code; recommend redact / effective allow; redaction preview |
| `redact.network.input` | `privacy-patterns-redact-v1` | `Server 192.168.1.10 has MAC 01:23:45:67:89:AB` | complete; pii.ip_address, pii.mac_address; recommend redact / effective allow; redaction preview |
| `redact.network.output` | `privacy-patterns-redact-v1` | `Server 192.168.1.10 has MAC 01:23:45:67:89:AB` | complete; pii.ip_address, pii.mac_address; recommend redact / effective allow; redaction preview |
| `redact.json.input` | `privacy-patterns-redact-v1` | `{"ni_number": "AB123456C", "note": "ok"}` | complete; pii.uk_nino; recommend redact / effective allow; redaction preview |
| `redact.json.output` | `privacy-patterns-redact-v1` | `{"ni_number": "AB123456C", "note": "ok"}` | complete; pii.uk_nino; recommend redact / effective allow; redaction preview |
| `redact.clean.input` | `privacy-patterns-redact-v1` | `Can you explain your return policy?` | complete; passed; recommend allow / effective allow; redaction not_needed |
| `redact.clean.output` | `privacy-patterns-redact-v1` | `Can you explain your return policy?` | complete; passed; recommend allow / effective allow; redaction not_needed |

Run them with `py -3.13 start_dashboard.py --evaluate --suite privacy-patterns`.
