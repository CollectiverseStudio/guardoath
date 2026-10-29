# Copied privacy pattern code

These files are copied from the Data Privacy Stack privacy analyzer, release 2.2.366 (MIT license).
The copyright notice the license requires is kept in `LICENSE`.

GuardOath includes every recognizer from that release that works without an NLP model or a
remote service: 93 recognizers (one per entity type) in 88 recognizer files, plus `iban_patterns.py`
and the 7 shared base files. Recognizers that need spaCy, Stanza, transformers or GLiNER
models (names, locations and other NER types) and the Azure, Azure OpenAI and LangExtract
connectors are not copied.

Every source file was checked against the release before copying (git blob hashes of all 96
files match the 2.2.366 tag). The table lists each copy, its path inside the analyzer package,
and exactly what changed. Line endings follow this repository (CRLF).

| Copied file | Source path in the analyzer package | Changes |
| --- | --- | --- |
| `pattern.py` | `pattern.py` | none (byte-for-byte copy apart from line endings) |
| `pattern_recognizer.py` | `pattern_recognizer.py` | NLP-engine type import removed (no NLP engine is included); empty `if TYPE_CHECKING:` block removed; logger name changed to `guardoath.privacy`; package import of the shared classes rewritten as relative imports; unused `TYPE_CHECKING` name removed from the typing import |
| `local_recognizer.py` | `local_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `entity_recognizer.py` | `entity_recognizer.py` | NLP-engine type import removed (no NLP engine is included); empty `if TYPE_CHECKING:` block removed; import of a shared module rewritten as a relative import; logger name changed to `guardoath.privacy`; package import of the shared classes rewritten as relative imports; unused `TYPE_CHECKING` name removed from the typing import |
| `recognizer_result.py` | `recognizer_result.py` | logger name changed to `guardoath.privacy`; package import of the shared classes rewritten as relative imports |
| `analysis_explanation.py` | `analysis_explanation.py` | none (byte-for-byte copy apart from line endings) |
| `score_thresholds.py` | `score_thresholds.py` | none (byte-for-byte copy apart from line endings) |
| `au_abn_recognizer.py` | `predefined_recognizers/country_specific/australia/au_abn_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `au_acn_recognizer.py` | `predefined_recognizers/country_specific/australia/au_acn_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `au_medicare_recognizer.py` | `predefined_recognizers/country_specific/australia/au_medicare_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `au_tfn_recognizer.py` | `predefined_recognizers/country_specific/australia/au_tfn_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `ca_postal_code_recognizer.py` | `predefined_recognizers/country_specific/canada/ca_postal_code_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `ca_sin_recognizer.py` | `predefined_recognizers/country_specific/canada/ca_sin_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `fi_personal_identity_code_recognizer.py` | `predefined_recognizers/country_specific/finland/fi_personal_identity_code_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `de_bsnr_recognizer.py` | `predefined_recognizers/country_specific/germany/de_bsnr_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `de_fuehrerschein_recognizer.py` | `predefined_recognizers/country_specific/germany/de_fuehrerschein_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `de_handelsregister_recognizer.py` | `predefined_recognizers/country_specific/germany/de_handelsregister_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `de_health_insurance_recognizer.py` | `predefined_recognizers/country_specific/germany/de_health_insurance_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `de_id_card_recognizer.py` | `predefined_recognizers/country_specific/germany/de_id_card_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `de_kfz_recognizer.py` | `predefined_recognizers/country_specific/germany/de_kfz_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `de_lanr_recognizer.py` | `predefined_recognizers/country_specific/germany/de_lanr_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `de_passport_recognizer.py` | `predefined_recognizers/country_specific/germany/de_passport_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `de_plz_recognizer.py` | `predefined_recognizers/country_specific/germany/de_plz_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `de_social_security_recognizer.py` | `predefined_recognizers/country_specific/germany/de_social_security_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `de_tax_id_recognizer.py` | `predefined_recognizers/country_specific/germany/de_tax_id_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `de_tax_number_recognizer.py` | `predefined_recognizers/country_specific/germany/de_tax_number_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `de_vat_id_recognizer.py` | `predefined_recognizers/country_specific/germany/de_vat_id_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `in_aadhaar_recognizer.py` | `predefined_recognizers/country_specific/india/in_aadhaar_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `in_gstin_recognizer.py` | `predefined_recognizers/country_specific/india/in_gstin_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `in_pan_recognizer.py` | `predefined_recognizers/country_specific/india/in_pan_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `in_passport_recognizer.py` | `predefined_recognizers/country_specific/india/in_passport_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `in_vehicle_registration_recognizer.py` | `predefined_recognizers/country_specific/india/in_vehicle_registration_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `in_voter_recognizer.py` | `predefined_recognizers/country_specific/india/in_voter_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `it_driver_license_recognizer.py` | `predefined_recognizers/country_specific/italy/it_driver_license_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `it_fiscal_code_recognizer.py` | `predefined_recognizers/country_specific/italy/it_fiscal_code_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `it_identity_card_recognizer.py` | `predefined_recognizers/country_specific/italy/it_identity_card_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `it_passport_recognizer.py` | `predefined_recognizers/country_specific/italy/it_passport_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `it_vat_code.py` | `predefined_recognizers/country_specific/italy/it_vat_code.py` | package import of the shared classes rewritten as relative imports |
| `kr_brn_recognizer.py` | `predefined_recognizers/country_specific/korea/kr_brn_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `kr_driver_license_recognizer.py` | `predefined_recognizers/country_specific/korea/kr_driver_license_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `kr_frn_recognizer.py` | `predefined_recognizers/country_specific/korea/kr_frn_recognizer.py` | import of a sibling recognizer rewritten as a relative import; package import of the shared classes rewritten as relative imports |
| `kr_passport_recognizer.py` | `predefined_recognizers/country_specific/korea/kr_passport_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `kr_rrn_recognizer.py` | `predefined_recognizers/country_specific/korea/kr_rrn_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `ng_nin_recognizer.py` | `predefined_recognizers/country_specific/nigeria/ng_nin_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `ng_vehicle_registration_recognizer.py` | `predefined_recognizers/country_specific/nigeria/ng_vehicle_registration_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `ph_passport_recognizer.py` | `predefined_recognizers/country_specific/philippines/ph_passport_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `ph_tin_recognizer.py` | `predefined_recognizers/country_specific/philippines/ph_tin_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `ph_umid_recognizer.py` | `predefined_recognizers/country_specific/philippines/ph_umid_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `pl_pesel_recognizer.py` | `predefined_recognizers/country_specific/poland/pl_pesel_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `sg_fin_recognizer.py` | `predefined_recognizers/country_specific/singapore/sg_fin_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `sg_uen_recognizer.py` | `predefined_recognizers/country_specific/singapore/sg_uen_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `za_company_registration_recognizer.py` | `predefined_recognizers/country_specific/south_africa/za_company_registration_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `za_driver_license_recognizer.py` | `predefined_recognizers/country_specific/south_africa/za_driver_license_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `za_id_number_recognizer.py` | `predefined_recognizers/country_specific/south_africa/za_id_number_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `za_income_tax_number_recognizer.py` | `predefined_recognizers/country_specific/south_africa/za_income_tax_number_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `za_license_plate_recognizer.py` | `predefined_recognizers/country_specific/south_africa/za_license_plate_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `za_passport_recognizer.py` | `predefined_recognizers/country_specific/south_africa/za_passport_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `za_phone_number_recognizer.py` | `predefined_recognizers/country_specific/south_africa/za_phone_number_recognizer.py` | NLP-engine type import removed (no NLP engine is included); `NlpArtifacts` type hints written as strings; import of a sibling recognizer rewritten as a relative import; package import of the shared classes rewritten as relative imports |
| `za_traffic_register_number_recognizer.py` | `predefined_recognizers/country_specific/south_africa/za_traffic_register_number_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `za_vat_number_recognizer.py` | `predefined_recognizers/country_specific/south_africa/za_vat_number_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `es_nie_recognizer.py` | `predefined_recognizers/country_specific/spain/es_nie_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `es_nif_recognizer.py` | `predefined_recognizers/country_specific/spain/es_nif_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `es_passport_recognizer.py` | `predefined_recognizers/country_specific/spain/es_passport_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `se_organisationsnummer_recognizer.py` | `predefined_recognizers/country_specific/sweden/se_organisationsnummer_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `se_personnummer_recognizer.py` | `predefined_recognizers/country_specific/sweden/se_personnummer_recognizer.py` | one comment reworded (it named the upstream project); package import of the shared classes rewritten as relative imports |
| `th_tnin_recognizer.py` | `predefined_recognizers/country_specific/thai/th_tnin_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `tr_license_plate_recognizer.py` | `predefined_recognizers/country_specific/turkey/tr_license_plate_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `tr_national_id_recognizer.py` | `predefined_recognizers/country_specific/turkey/tr_national_id_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `uk_driving_licence_recognizer.py` | `predefined_recognizers/country_specific/uk/uk_driving_licence_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `uk_nhs_recognizer.py` | `predefined_recognizers/country_specific/uk/uk_nhs_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `uk_nino_recognizer.py` | `predefined_recognizers/country_specific/uk/uk_nino_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `uk_passport_recognizer.py` | `predefined_recognizers/country_specific/uk/uk_passport_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `uk_postcode_recognizer.py` | `predefined_recognizers/country_specific/uk/uk_postcode_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `uk_vehicle_registration_recognizer.py` | `predefined_recognizers/country_specific/uk/uk_vehicle_registration_recognizer.py` | import of a shared module rewritten as a relative import; package import of the shared classes rewritten as relative imports |
| `aba_routing_recognizer.py` | `predefined_recognizers/country_specific/us/aba_routing_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `medical_license_recognizer.py` | `predefined_recognizers/country_specific/us/medical_license_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `us_bank_recognizer.py` | `predefined_recognizers/country_specific/us/us_bank_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `us_driver_license_recognizer.py` | `predefined_recognizers/country_specific/us/us_driver_license_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `us_health_insurance_member_id_recognizer.py` | `predefined_recognizers/country_specific/us/us_health_insurance_member_id_recognizer.py` | one comment reworded (it named the upstream project); package import of the shared classes rewritten as relative imports |
| `us_healthcare_admin_recognizers.py` | `predefined_recognizers/country_specific/us/us_healthcare_admin_recognizers.py` | package import of the shared classes rewritten as relative imports |
| `us_itin_recognizer.py` | `predefined_recognizers/country_specific/us/us_itin_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `us_mbi_recognizer.py` | `predefined_recognizers/country_specific/us/us_mbi_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `us_npi_recognizer.py` | `predefined_recognizers/country_specific/us/us_npi_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `us_passport_recognizer.py` | `predefined_recognizers/country_specific/us/us_passport_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `us_ssn_recognizer.py` | `predefined_recognizers/country_specific/us/us_ssn_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `credit_card_recognizer.py` | `predefined_recognizers/generic/credit_card_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `crypto_recognizer.py` | `predefined_recognizers/generic/crypto_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `date_recognizer.py` | `predefined_recognizers/generic/date_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `email_recognizer.py` | `predefined_recognizers/generic/email_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `iban_patterns.py` | `predefined_recognizers/generic/iban_patterns.py` | none (byte-for-byte copy apart from line endings) |
| `iban_recognizer.py` | `predefined_recognizers/generic/iban_recognizer.py` | NLP-engine type import removed (no NLP engine is included); `NlpArtifacts` type hints written as strings; import of a sibling recognizer rewritten as a relative import; logger name changed to `guardoath.privacy`; package import of the shared classes rewritten as relative imports |
| `ip_recognizer.py` | `predefined_recognizers/generic/ip_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `mac_recognizer.py` | `predefined_recognizers/generic/mac_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `phone_recognizer.py` | `predefined_recognizers/generic/phone_recognizer.py` | NLP-engine type import removed (no NLP engine is included); `NlpArtifacts` type hints written as strings; package import of the shared classes rewritten as relative imports |
| `url_recognizer.py` | `predefined_recognizers/generic/url_recognizer.py` | package import of the shared classes rewritten as relative imports |
| `uuid_recognizer.py` | `predefined_recognizers/generic/uuid_recognizer.py` | package import of the shared classes rewritten as relative imports |

`__init__.py`, this README and `LICENSE` were added. `__init__.py` exports the classes,
`SOURCE_VERSION = "2.2.366"` and `RECOGNIZER_CLASSES` (the 93 recognizers that can be created
without arguments).

How GuardOath uses this code is described in `guardoath/pii.py` and in the user guide
(section "Privacy pattern checks"). The copied upstream tests are in `tests/privacy_patterns/`.
