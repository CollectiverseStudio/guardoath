# Copied privacy pattern tests

The upstream tests for the copied privacy pattern code (Data Privacy Stack privacy analyzer,
release 2.2.366, MIT license; see `guardoath/privacy_patterns/LICENSE`). Each file was checked
against the release by git blob hash before copying. Test cases are unchanged, except two
sample domains renamed in the email and URL tests (listed in the table).

92 test files, 253 test functions, 2052 pytest items. `privacy_assertions.py` is the
upstream `assertions.py`; `conftest.py` provides the two upstream fixtures these files use.

Common changes in every test file: imports point at `guardoath.privacy_patterns` and
`privacy_assertions`, and two `pytest.importorskip` lines skip the file when the optional `pii`
dependencies (tldextract, phonenumbers) are not installed.

| File | Items | Changes |
| --- | --- | --- |
| `privacy_assertions.py` | helper | renamed from `assertions.py`; no other change |
| `test_aba_routing_recognizer.py` | 6 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_au_abn_recognizer.py` | 9 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_au_acn_recognizer.py` | 10 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_au_medicare_recognizer.py` | 7 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_au_tfn_recognizer.py` | 8 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_ca_postal_code_recognizer.py` | 24 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_ca_sin_recognizer.py` | 20 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_credit_card_recognizer.py` | 19 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_crypto_recognizer.py` | 10 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_date_recognizer.py` | 41 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_de_bsnr_recognizer.py` | 23 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_de_fuehrerschein_recognizer.py` | 14 | imports point at `guardoath.privacy_patterns`; one comment reworded (it named the upstream project); skips when the optional `pii` dependencies are missing |
| `test_de_handelsregister_recognizer.py` | 13 | imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_de_health_insurance_recognizer.py` | 29 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; one comment reworded (it named the upstream project); skips when the optional `pii` dependencies are missing |
| `test_de_id_card_recognizer.py` | 28 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_de_kfz_recognizer.py` | 17 | imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_de_lanr_recognizer.py` | 25 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_de_passport_recognizer.py` | 29 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_de_plz_recognizer.py` | 14 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_de_social_security_recognizer.py` | 26 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_de_tax_id_recognizer.py` | 22 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_de_tax_number_recognizer.py` | 14 | imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_de_vat_id_recognizer.py` | 56 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_email_recognizer.py` | 8 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; sample domain renamed to `examples.*` (same length, so expected positions are unchanged); skips when the optional `pii` dependencies are missing |
| `test_entity_recognizer.py` | 32 | imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_es_nie_recognizer.py` | 16 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_es_nif_recognizer.py` | 10 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_es_passport_recognizer.py` | 15 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_fi_personal_identity_code_recognizer.py` | 38 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_iban_recognizer.py` | 333 | `mock.patch` targets point at the copied module; assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_in_aadhaar_recognizer.py` | 21 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_in_gstin_recognizer.py` | 55 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_in_pan_recognizer.py` | 6 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_in_passport_recognizer.py` | 7 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_in_vehicle_registration_recognizer.py` | 11 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_in_voter_recognizer.py` | 7 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_ip_recognizer.py` | 115 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_it_driver_license_recognizer.py` | 7 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_it_fiscal_code_recognizer.py` | 5 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_it_identity_card_recognizer.py` | 3 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_it_passport_recognizer.py` | 2 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_it_vat_code_recognizer.py` | 5 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_kr_brn_recognizer.py` | 9 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_kr_driver_license_recognizer.py` | 10 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_kr_frn_recognizer.py` | 18 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_kr_passport_recognizer.py` | 38 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; imports used only by the left-out test removed; skips when the optional `pii` dependencies are missing |
| `test_kr_rrn_recognizer.py` | 18 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_mac_recognizer.py` | 20 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_ng_nin_recognizer.py` | 9 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_ng_vehicle_registration_recognizer.py` | 13 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_pattern.py` | 6 | imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_pattern_recognizer.py` | 45 | `mock.patch` targets point at the copied module; assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_ph_passport_recognizer.py` | 17 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_ph_tin_recognizer.py` | 10 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_ph_umid_recognizer.py` | 34 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_phone_recognizer.py` | 27 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_pl_pesel_recognizer.py` | 18 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_recognizer_result.py` | 38 | imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_se_organisationsnummer_recognizer.py` | 11 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_se_personnummer_recognizer.py` | 18 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_sg_fin_recognizer.py` | 11 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_sg_uen_recognizer.py` | 11 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_th_tnin_recognizer.py` | 46 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_tr_license_plate_recognizer.py` | 25 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_tr_national_id_recognizer.py` | 32 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_uk_driving_licence_recognizer.py` | 21 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_uk_nhs_recognizer.py` | 4 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_uk_nino_recognizer.py` | 16 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_uk_passport_recognizer.py` | 13 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_uk_postcode_recognizer.py` | 21 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_uk_vehicle_registration_recognizer.py` | 27 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_url_recognizer.py` | 18 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; sample domain renamed to `examples.*` (same length, so expected positions are unchanged); skips when the optional `pii` dependencies are missing |
| `test_us_bank_recognizer.py` | 2 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_us_driver_license_recognizer.py` | 5 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_us_health_insurance_member_id_recognizer.py` | 1 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; names used only by the left-out tests removed from the imports; skips when the optional `pii` dependencies are missing |
| `test_us_healthcare_admin_recognizers.py` | 5 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; names used only by the left-out tests removed from the imports; skips when the optional `pii` dependencies are missing |
| `test_us_itin_recognizer.py` | 7 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_us_mbi_recognizer.py` | 20 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; one comment reworded (it named the upstream project); skips when the optional `pii` dependencies are missing |
| `test_us_npi_recognizer.py` | 18 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_us_passport_recognizer.py` | 5 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_us_ssn_recognizer.py` | 28 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_uuid_recognizer.py` | 19 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_za_company_registration_recognizer.py` | 16 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_za_driver_license_recognizer.py` | 16 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_za_id_number_recognizer.py` | 33 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_za_income_tax_number_recognizer.py` | 14 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_za_license_plate_recognizer.py` | 18 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_za_mobile_number_recognizer.py` | 24 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_za_passport_recognizer.py` | 18 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_za_telephone_number_recognizer.py` | 16 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_za_traffic_register_number_recognizer.py` | 10 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |
| `test_za_vat_number_recognizer.py` | 13 | assertion helpers imported from `privacy_assertions`; imports point at `guardoath.privacy_patterns`; skips when the optional `pii` dependencies are missing |

## Tests left out

These upstream tests need parts of the analyzer that are not copied (the analyzer engine with
a spaCy NLP model, or the recognizer registry and its YAML configuration). They are left out
rather than faked: 22 test functions, 120 pytest items, plus the two fixtures
that build the engine. GuardOath covers these recognizers in `tests/test_privacy_patterns.py`
and `evals/privacy-patterns.json` instead.

| File | Test | Items | Reason |
| --- | --- | --- | --- |
| `test_kr_passport_recognizer.py` | `test_loads_from_default_recognizers_yaml` | 2 | loads the upstream recognizer registry and its YAML configuration, which are not copied |
| `test_us_health_insurance_member_id_recognizer.py` | `analyze_member_id` (fixture) | - | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_health_insurance_member_id_recognizer.py` | `test_when_member_id_has_context_then_detected` | 8 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_health_insurance_member_id_recognizer.py` | `test_member_id_matching_is_case_insensitive_and_ignores_trailing_punctuation` | 3 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_health_insurance_member_id_recognizer.py` | `test_when_text_has_multiple_member_ids_then_all_are_detected` | 1 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_health_insurance_member_id_recognizer.py` | `test_member_id_minimum_and_maximum_lengths_are_detected` | 2 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_health_insurance_member_id_recognizer.py` | `test_when_member_id_lacks_insurance_context_then_below_threshold` | 13 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_health_insurance_member_id_recognizer.py` | `test_when_member_id_pattern_is_implausible_then_not_detected` | 3 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_health_insurance_member_id_recognizer.py` | `test_explicit_request_threshold_can_return_pattern_only_member_id` | 1 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_healthcare_admin_recognizers.py` | `analyze_with_recognizer` (fixture) | - | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_healthcare_admin_recognizers.py` | `test_when_us_healthcare_admin_id_has_context_then_detected` | 5 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_healthcare_admin_recognizers.py` | `test_admin_id_matching_is_case_insensitive` | 5 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_healthcare_admin_recognizers.py` | `test_when_text_has_multiple_admin_ids_then_all_are_detected` | 5 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_healthcare_admin_recognizers.py` | `test_admin_id_matching_ignores_trailing_punctuation` | 5 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_healthcare_admin_recognizers.py` | `test_admin_id_minimum_and_maximum_lengths_are_detected` | 8 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_healthcare_admin_recognizers.py` | `test_too_short_and_too_long_admin_ids_do_not_match` | 10 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_healthcare_admin_recognizers.py` | `test_when_admin_id_follows_label_then_identifier_only_is_detected` | 7 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_healthcare_admin_recognizers.py` | `test_when_provider_ein_has_provider_tax_label_then_detected` | 6 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_healthcare_admin_recognizers.py` | `test_when_number_has_different_workflow_label_then_prescription_not_detected` | 1 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_healthcare_admin_recognizers.py` | `test_when_us_healthcare_admin_id_lacks_context_then_below_threshold` | 5 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_healthcare_admin_recognizers.py` | `test_when_us_healthcare_admin_id_has_unrelated_context_then_not_detected` | 5 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_healthcare_admin_recognizers.py` | `test_when_ein_lacks_provider_tax_label_then_not_detected` | 3 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_healthcare_admin_recognizers.py` | `test_when_provider_ein_prefix_is_not_irs_valid_then_not_detected` | 17 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |
| `test_us_healthcare_admin_recognizers.py` | `test_explicit_request_threshold_can_return_pattern_only_matches` | 5 | needs the upstream AnalyzerEngine with a spaCy NLP engine (context scoring through the analyzer); not copied |

The phone tests ask for a spaCy engine only to pass NLP artifacts that the copied phone
recognizers ignore; `conftest.py` supplies a stand-in that returns `None`, so those tests run
unchanged without spaCy.
