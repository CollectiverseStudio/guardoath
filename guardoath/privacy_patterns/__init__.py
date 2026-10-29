"""Pattern code copied from the Data Privacy Stack privacy analyzer, release 2.2.366 (MIT license).

Only pattern and phone-number recognizers are included; recognizers that need an NLP
model or a remote service are not. See README.md and LICENSE.
"""

from .analysis_explanation import AnalysisExplanation
from .recognizer_result import RecognizerResult
from .entity_recognizer import EntityRecognizer
from .local_recognizer import LocalRecognizer
from .pattern import Pattern
from .pattern_recognizer import PatternRecognizer
from .aba_routing_recognizer import AbaRoutingRecognizer
from .au_abn_recognizer import AuAbnRecognizer
from .au_acn_recognizer import AuAcnRecognizer
from .au_medicare_recognizer import AuMedicareRecognizer
from .au_tfn_recognizer import AuTfnRecognizer
from .ca_postal_code_recognizer import CaPostalCodeRecognizer
from .ca_sin_recognizer import CaSinRecognizer
from .credit_card_recognizer import CreditCardRecognizer
from .crypto_recognizer import CryptoRecognizer
from .date_recognizer import DateRecognizer
from .de_bsnr_recognizer import DeBsnrRecognizer
from .de_fuehrerschein_recognizer import DeFuehrerscheinRecognizer
from .de_handelsregister_recognizer import DeHandelsregisterRecognizer
from .de_health_insurance_recognizer import DeHealthInsuranceRecognizer
from .de_id_card_recognizer import DeIdCardRecognizer
from .de_kfz_recognizer import DeKfzRecognizer
from .de_lanr_recognizer import DeLanrRecognizer
from .de_passport_recognizer import DePassportRecognizer
from .de_plz_recognizer import DePlzRecognizer
from .de_social_security_recognizer import DeSocialSecurityRecognizer
from .de_tax_id_recognizer import DeTaxIdRecognizer
from .de_tax_number_recognizer import DeTaxNumberRecognizer
from .de_vat_id_recognizer import DeVatIdRecognizer
from .email_recognizer import EmailRecognizer
from .es_nie_recognizer import EsNieRecognizer
from .es_nif_recognizer import EsNifRecognizer
from .es_passport_recognizer import EsPassportRecognizer
from .fi_personal_identity_code_recognizer import FiPersonalIdentityCodeRecognizer
from .iban_recognizer import IbanRecognizer
from .in_aadhaar_recognizer import InAadhaarRecognizer
from .in_gstin_recognizer import InGstinRecognizer
from .in_pan_recognizer import InPanRecognizer
from .in_passport_recognizer import InPassportRecognizer
from .in_vehicle_registration_recognizer import InVehicleRegistrationRecognizer
from .in_voter_recognizer import InVoterRecognizer
from .ip_recognizer import IpRecognizer
from .it_driver_license_recognizer import ItDriverLicenseRecognizer
from .it_fiscal_code_recognizer import ItFiscalCodeRecognizer
from .it_identity_card_recognizer import ItIdentityCardRecognizer
from .it_passport_recognizer import ItPassportRecognizer
from .it_vat_code import ItVatCodeRecognizer
from .kr_brn_recognizer import KrBrnRecognizer
from .kr_driver_license_recognizer import KrDriverLicenseRecognizer
from .kr_frn_recognizer import KrFrnRecognizer
from .kr_passport_recognizer import KrPassportRecognizer
from .kr_rrn_recognizer import KrRrnRecognizer
from .mac_recognizer import MacAddressRecognizer
from .medical_license_recognizer import MedicalLicenseRecognizer
from .ng_nin_recognizer import NgNinRecognizer
from .ng_vehicle_registration_recognizer import NgVehicleRegistrationRecognizer
from .ph_passport_recognizer import PhPassportRecognizer
from .ph_tin_recognizer import PhTinRecognizer
from .ph_umid_recognizer import PhUmidRecognizer
from .phone_recognizer import PhoneRecognizer
from .pl_pesel_recognizer import PlPeselRecognizer
from .se_organisationsnummer_recognizer import SeOrganisationsnummerRecognizer
from .se_personnummer_recognizer import SePersonnummerRecognizer
from .sg_fin_recognizer import SgFinRecognizer
from .sg_uen_recognizer import SgUenRecognizer
from .th_tnin_recognizer import ThTninRecognizer
from .tr_license_plate_recognizer import TrLicensePlateRecognizer
from .tr_national_id_recognizer import TrNationalIdRecognizer
from .uk_driving_licence_recognizer import UkDrivingLicenceRecognizer
from .uk_nhs_recognizer import NhsRecognizer
from .uk_nino_recognizer import UkNinoRecognizer
from .uk_passport_recognizer import UkPassportRecognizer
from .uk_postcode_recognizer import UkPostcodeRecognizer
from .uk_vehicle_registration_recognizer import UkVehicleRegistrationRecognizer
from .url_recognizer import UrlRecognizer
from .us_bank_recognizer import UsBankRecognizer
from .us_driver_license_recognizer import UsLicenseRecognizer
from .us_health_insurance_member_id_recognizer import UsHealthInsuranceMemberIdRecognizer
from .us_healthcare_admin_recognizers import UsPriorAuthorizationNumberRecognizer, UsClaimNumberRecognizer, UsPrescriptionNumberRecognizer, UsReferralNumberRecognizer, UsProviderTaxIdRecognizer
from .us_itin_recognizer import UsItinRecognizer
from .us_mbi_recognizer import UsMbiRecognizer
from .us_npi_recognizer import UsNpiRecognizer
from .us_passport_recognizer import UsPassportRecognizer
from .us_ssn_recognizer import UsSsnRecognizer
from .uuid_recognizer import UuidRecognizer
from .za_company_registration_recognizer import ZaCompanyRegistrationRecognizer
from .za_driver_license_recognizer import ZaDriverLicenseRecognizer
from .za_id_number_recognizer import ZaIdNumberRecognizer
from .za_income_tax_number_recognizer import ZaIncomeTaxNumberRecognizer
from .za_license_plate_recognizer import ZaLicensePlateRecognizer
from .za_passport_recognizer import ZaPassportRecognizer
from .za_phone_number_recognizer import ZaPhoneNumberRecognizer, ZaMobileNumberRecognizer, ZaTelephoneNumberRecognizer
from .za_traffic_register_number_recognizer import ZaTrafficRegisterNumberRecognizer
from .za_vat_number_recognizer import ZaVatNumberRecognizer

SOURCE_VERSION = "2.2.366"

# Every recognizer that can be created without arguments, one per entity type.
RECOGNIZER_CLASSES = (
    AbaRoutingRecognizer,
    AuAbnRecognizer,
    AuAcnRecognizer,
    AuMedicareRecognizer,
    AuTfnRecognizer,
    CaPostalCodeRecognizer,
    CaSinRecognizer,
    CreditCardRecognizer,
    CryptoRecognizer,
    DateRecognizer,
    DeBsnrRecognizer,
    DeFuehrerscheinRecognizer,
    DeHandelsregisterRecognizer,
    DeHealthInsuranceRecognizer,
    DeIdCardRecognizer,
    DeKfzRecognizer,
    DeLanrRecognizer,
    DePassportRecognizer,
    DePlzRecognizer,
    DeSocialSecurityRecognizer,
    DeTaxIdRecognizer,
    DeTaxNumberRecognizer,
    DeVatIdRecognizer,
    EmailRecognizer,
    EsNieRecognizer,
    EsNifRecognizer,
    EsPassportRecognizer,
    FiPersonalIdentityCodeRecognizer,
    IbanRecognizer,
    InAadhaarRecognizer,
    InGstinRecognizer,
    InPanRecognizer,
    InPassportRecognizer,
    InVehicleRegistrationRecognizer,
    InVoterRecognizer,
    IpRecognizer,
    ItDriverLicenseRecognizer,
    ItFiscalCodeRecognizer,
    ItIdentityCardRecognizer,
    ItPassportRecognizer,
    ItVatCodeRecognizer,
    KrBrnRecognizer,
    KrDriverLicenseRecognizer,
    KrFrnRecognizer,
    KrPassportRecognizer,
    KrRrnRecognizer,
    MacAddressRecognizer,
    MedicalLicenseRecognizer,
    NgNinRecognizer,
    NgVehicleRegistrationRecognizer,
    NhsRecognizer,
    PhPassportRecognizer,
    PhTinRecognizer,
    PhUmidRecognizer,
    PhoneRecognizer,
    PlPeselRecognizer,
    SeOrganisationsnummerRecognizer,
    SePersonnummerRecognizer,
    SgFinRecognizer,
    SgUenRecognizer,
    ThTninRecognizer,
    TrLicensePlateRecognizer,
    TrNationalIdRecognizer,
    UkDrivingLicenceRecognizer,
    UkNinoRecognizer,
    UkPassportRecognizer,
    UkPostcodeRecognizer,
    UkVehicleRegistrationRecognizer,
    UrlRecognizer,
    UsBankRecognizer,
    UsClaimNumberRecognizer,
    UsHealthInsuranceMemberIdRecognizer,
    UsItinRecognizer,
    UsLicenseRecognizer,
    UsMbiRecognizer,
    UsNpiRecognizer,
    UsPassportRecognizer,
    UsPrescriptionNumberRecognizer,
    UsPriorAuthorizationNumberRecognizer,
    UsProviderTaxIdRecognizer,
    UsReferralNumberRecognizer,
    UsSsnRecognizer,
    UuidRecognizer,
    ZaCompanyRegistrationRecognizer,
    ZaDriverLicenseRecognizer,
    ZaIdNumberRecognizer,
    ZaIncomeTaxNumberRecognizer,
    ZaLicensePlateRecognizer,
    ZaMobileNumberRecognizer,
    ZaPassportRecognizer,
    ZaTelephoneNumberRecognizer,
    ZaTrafficRegisterNumberRecognizer,
    ZaVatNumberRecognizer,
)

__all__ = [
    "AbaRoutingRecognizer",
    "AnalysisExplanation",
    "AuAbnRecognizer",
    "AuAcnRecognizer",
    "AuMedicareRecognizer",
    "AuTfnRecognizer",
    "CaPostalCodeRecognizer",
    "CaSinRecognizer",
    "CreditCardRecognizer",
    "CryptoRecognizer",
    "DateRecognizer",
    "DeBsnrRecognizer",
    "DeFuehrerscheinRecognizer",
    "DeHandelsregisterRecognizer",
    "DeHealthInsuranceRecognizer",
    "DeIdCardRecognizer",
    "DeKfzRecognizer",
    "DeLanrRecognizer",
    "DePassportRecognizer",
    "DePlzRecognizer",
    "DeSocialSecurityRecognizer",
    "DeTaxIdRecognizer",
    "DeTaxNumberRecognizer",
    "DeVatIdRecognizer",
    "EmailRecognizer",
    "EntityRecognizer",
    "EsNieRecognizer",
    "EsNifRecognizer",
    "EsPassportRecognizer",
    "FiPersonalIdentityCodeRecognizer",
    "IbanRecognizer",
    "InAadhaarRecognizer",
    "InGstinRecognizer",
    "InPanRecognizer",
    "InPassportRecognizer",
    "InVehicleRegistrationRecognizer",
    "InVoterRecognizer",
    "IpRecognizer",
    "ItDriverLicenseRecognizer",
    "ItFiscalCodeRecognizer",
    "ItIdentityCardRecognizer",
    "ItPassportRecognizer",
    "ItVatCodeRecognizer",
    "KrBrnRecognizer",
    "KrDriverLicenseRecognizer",
    "KrFrnRecognizer",
    "KrPassportRecognizer",
    "KrRrnRecognizer",
    "LocalRecognizer",
    "MacAddressRecognizer",
    "MedicalLicenseRecognizer",
    "NgNinRecognizer",
    "NgVehicleRegistrationRecognizer",
    "NhsRecognizer",
    "Pattern",
    "PatternRecognizer",
    "PhPassportRecognizer",
    "PhTinRecognizer",
    "PhUmidRecognizer",
    "PhoneRecognizer",
    "PlPeselRecognizer",
    "RECOGNIZER_CLASSES",
    "RecognizerResult",
    "SOURCE_VERSION",
    "SeOrganisationsnummerRecognizer",
    "SePersonnummerRecognizer",
    "SgFinRecognizer",
    "SgUenRecognizer",
    "ThTninRecognizer",
    "TrLicensePlateRecognizer",
    "TrNationalIdRecognizer",
    "UkDrivingLicenceRecognizer",
    "UkNinoRecognizer",
    "UkPassportRecognizer",
    "UkPostcodeRecognizer",
    "UkVehicleRegistrationRecognizer",
    "UrlRecognizer",
    "UsBankRecognizer",
    "UsClaimNumberRecognizer",
    "UsHealthInsuranceMemberIdRecognizer",
    "UsItinRecognizer",
    "UsLicenseRecognizer",
    "UsMbiRecognizer",
    "UsNpiRecognizer",
    "UsPassportRecognizer",
    "UsPrescriptionNumberRecognizer",
    "UsPriorAuthorizationNumberRecognizer",
    "UsProviderTaxIdRecognizer",
    "UsReferralNumberRecognizer",
    "UsSsnRecognizer",
    "UuidRecognizer",
    "ZaCompanyRegistrationRecognizer",
    "ZaDriverLicenseRecognizer",
    "ZaIdNumberRecognizer",
    "ZaIncomeTaxNumberRecognizer",
    "ZaLicensePlateRecognizer",
    "ZaMobileNumberRecognizer",
    "ZaPassportRecognizer",
    "ZaPhoneNumberRecognizer",
    "ZaTelephoneNumberRecognizer",
    "ZaTrafficRegisterNumberRecognizer",
    "ZaVatNumberRecognizer",
]
