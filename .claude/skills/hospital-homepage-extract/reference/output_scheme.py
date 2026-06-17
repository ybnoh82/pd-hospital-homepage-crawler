"""hospital-homepage-extract 출력 스키마.

홈페이지에서 수집한 데이터를 관계형 DB에 적재하기 좋은 형태로 담는다.
SKILL.md는 행동 규칙(어디를 방문하고, 언제 vision을 쓰고, 어떻게 판단하나)을 정의하고,
이 파일은 각 필드에 무엇을 어떤 형식으로 담는지를 정의한다.

직접 실행하면 출력 JSON 파일을 검증한다:
    uv run python output_scheme.py output/{병원ID}_{병원이름}_homepage.json
"""

from __future__ import annotations

import copy
import json
import sys
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

# 항목 단위 신뢰도.
# verified=홈페이지 원문에서 직접 확인, unverified=확인하지 못함, inferred=정황으로 추론.
VerificationStatus = Literal["verified", "unverified", "inferred"]


class OperatingHours(BaseModel):
    """하루치 영업시간. 시각은 24시간 "HH:MM" 표기."""

    open: str | None = Field(None, description='진료 시작 시각. 예: "10:00"')
    close: str | None = Field(None, description='진료 종료 시각. 예: "19:00"')
    break_start: str | None = Field(None, description="점심시간 시작 시각. 없으면 null")
    break_end: str | None = Field(None, description="점심시간 종료 시각. 없으면 null")


class OperationInfo(BaseModel):
    """병원 운영정보. 홈페이지의 소개·오시는길·푸터(사업자정보)에서 수집한다."""

    hospital_name: str | None = Field(
        None,
        description="홈페이지에 표기된 병원명. 입력받은 병원이름과 다를 수 있다"
        "(identity_status 판단 근거)",
    )
    representative_name: str | None = Field(None, description="대표자(대표원장) 이름")
    business_number: str | None = Field(
        None, description='사업자등록번호. "000-00-00000" 형식으로 정규화'
    )
    phone: str | None = Field(
        None, description='대표 전화번호. "02-0000-0000" 형식으로 정규화'
    )
    phone_secondary: str | None = Field(
        None, description="추가 전화번호(상담 전용 등). 형식은 phone과 동일"
    )
    fax: str | None = Field(None, description="팩스 번호")
    email: str | None = Field(None, description="대표 이메일")
    kakao_channel: str | None = Field(None, description="카카오톡 채널 URL 또는 채널명")
    operating_hours: dict[str, OperatingHours | None] | None = Field(
        None,
        description="요일별 영업시간. 키는 monday~sunday와 holiday(공휴일). "
        "휴무인 날은 null",
    )
    operating_hours_note: str | None = Field(
        None,
        description="야간진료·격주 휴무 등 요일 구조에 담기지 않는 특이사항(원문)",
    )
    parking_info: str | None = Field(None, description="주차 안내 원문")
    transport_info: str | None = Field(None, description="교통(오시는길) 안내 원문")
    facilities: list[str] = Field(
        [], description="시설·서비스 목록(주차장·와이파이 등)"
    )


class DoctorInfo(BaseModel):
    """의료진 1명. 의료진 소개 페이지에서 수집한다."""

    name: str = Field(description="의사 이름")
    is_representative: bool = Field(False, description="대표원장 여부")
    role: str | None = Field(None, description="직함. 예: 대표원장, 원장")
    specialty: list[str] = Field([], description="진료 분야·전문 과목")
    education: list[str] = Field([], description="학력(항목별 원문)")
    career: list[str] = Field([], description="경력(항목별 원문)")
    associations: list[str] = Field([], description="학회·협회 활동")
    awards: list[str] = Field([], description="수상·저서 등")
    profile_image_url: str | None = Field(None, description="프로필 사진 URL")
    verification_status: VerificationStatus | None = Field(
        None, description="이 항목의 신뢰도(VerificationStatus 참조)"
    )


class SourceRef(BaseModel):
    """한 항목의 출처 1건.

    같은 제품/장비가 여러 페이지에 나오면 출처를 모두 기록한다(통합 시 출처 보존).
    """

    channel: str = Field(
        "homepage", description='출처 채널. 이 스킬에서는 항상 "homepage"'
    )
    url: str | None = Field(
        None,
        description="해당 내용이 있는 페이지 URL. "
        '단일 페이지(앵커) 사이트면 "...#앵커"로 표기해 실제 페이지로 오인하지 않게 한다',
    )
    evidence: str | None = Field(None, description="근거가 된 본문 인용(짧게)")


class MatchedProduct(BaseModel):
    """카탈로그(aesthetic_products.json)에 매칭이 확정된 취급제품.

    product_kr·brand_kr·manufacturer_kr·category·plandocs_*는
    카탈로그 엔트리 값을 그대로 복사한다(직접 입력 금지).
    """

    product_kr: str = Field(description="카탈로그 정식 제품명(한글)")
    brand_kr: str | None = Field(None, description="카탈로그 브랜드명(한글)")
    manufacturer_kr: str | None = Field(None, description="카탈로그 제조사명(한글)")
    category: str | None = Field(
        None, description="카탈로그 카테고리. 예: 톡신, 필러, 스킨부스터"
    )
    plandocs_handled: int | None = Field(
        None, description="플랜닥스 취급 제품이면 1, 아니면 0 (카탈로그 값 복사)"
    )
    plandocs_featured: int | None = Field(
        None, description="플랜닥스 주력 제품이면 1, 아니면 0 (카탈로그 값 복사)"
    )
    mention_count: int | None = Field(
        None, description="홈페이지 본문에서 언급된 횟수(영업 신호)"
    )
    sources: list[SourceRef] = Field([], description="발견한 페이지별 출처")
    context: str | None = Field(
        None,
        description='매칭 근거·판단 사유. 예: "한↔영 교차 매칭", "라인 미특정, 대표 매칭". '
        "원문 인용은 sources[].evidence에 담는다",
    )
    verification_status: VerificationStatus | None = Field(
        None, description="이 항목의 신뢰도. 라인을 추정해 매칭했으면 inferred"
    )


class UnmatchedProduct(BaseModel):
    """제품으로 보이지만 카탈로그에 매칭하지 못한 항목.

    버리지 말고 여기 남긴다(무누락 원칙). 보내기 전에 카탈로그 재조회가 필수다(SKILL.md §3).
    """

    raw_name: str = Field(description="홈페이지 표기 그대로")
    category_guess: str | None = Field(None, description="추정 카테고리")
    mention_count: int | None = Field(None, description="홈페이지 본문에서 언급된 횟수")
    sources: list[SourceRef] = Field([], description="발견한 페이지별 출처")
    context: str | None = Field(
        None,
        description="보류 사유. 카탈로그 미수록이라 적으려면 재조회로 부재를 확인한 근거를 함께 남긴다",
    )
    verification_status: VerificationStatus | None = Field(
        None, description="이 항목의 신뢰도(VerificationStatus 참조)"
    )


class ProductsInfo(BaseModel):
    """취급제품 수집 결과(매칭 확정 + 보류)."""

    matched_products: list[MatchedProduct] = Field(
        [], description="카탈로그 매칭이 확정된 제품"
    )
    unmatched_products: list[UnmatchedProduct] = Field(
        [], description="매칭하지 못해 보류한 제품"
    )


class MatchedEquipment(BaseModel):
    """카탈로그(aesthetic_equipments.json)에 매칭이 확정된 취급장비.

    name_kr·name_en·category는 카탈로그 엔트리 값을 그대로 복사한다(직접 입력 금지).
    """

    name_kr: str = Field(description="카탈로그 정식 장비명(한글)")
    name_en: str | None = Field(None, description="카탈로그 장비명(영문)")
    category: str | None = Field(
        None, description="카탈로그 카테고리. 예: 리프팅, 레이저"
    )
    mention_count: int | None = Field(
        None, description="홈페이지 본문에서 언급된 횟수(영업 신호)"
    )
    sources: list[SourceRef] = Field([], description="발견한 페이지별 출처")
    context: str | None = Field(
        None, description="매칭 근거·판단 사유. 원문 인용은 sources[].evidence에 담는다"
    )
    verification_status: VerificationStatus | None = Field(
        None, description="이 항목의 신뢰도. 라인을 추정해 매칭했으면 inferred"
    )


class UnmatchedEquipment(BaseModel):
    """장비로 보이지만 카탈로그에 매칭하지 못한 항목. 버리지 말고 여기 남긴다."""

    raw_name: str = Field(description="홈페이지 표기 그대로")
    category_guess: str | None = Field(None, description="추정 카테고리")
    mention_count: int | None = Field(None, description="홈페이지 본문에서 언급된 횟수")
    sources: list[SourceRef] = Field([], description="발견한 페이지별 출처")
    context: str | None = Field(
        None,
        description="보류 사유. 카탈로그 미수록이라 적으려면 재조회로 부재를 확인한 근거를 함께 남긴다",
    )
    verification_status: VerificationStatus | None = Field(
        None, description="이 항목의 신뢰도(VerificationStatus 참조)"
    )


class EquipmentsInfo(BaseModel):
    """취급장비 수집 결과(매칭 확정 + 보류)."""

    matched_equipments: list[MatchedEquipment] = Field(
        [], description="카탈로그 매칭이 확정된 장비"
    )
    unmatched_equipments: list[UnmatchedEquipment] = Field(
        [], description="매칭하지 못해 보류한 장비"
    )


class ManufacturerSignal(BaseModel):
    """거래(취급) 정황이 확인된 제조사.

    제품까지 특정되지 않는 제조사 수준 증거를 담는다(제휴 배너·정품인증·본문 언급 등).
    matched_products에 잡힌 제품의 제조사를 여기 중복 기록하지 않는다 —
    제조사 집계는 통합 단계가 products에서 계산한다.
    """

    name: str = Field(description="제조사 이름(사이트 표기 그대로)")
    catalog_manufacturer_kr: str | None = Field(
        None, description="카탈로그 제조사명과 매칭되면 그 정식 표기(예: 멀츠)"
    )
    sources: list[SourceRef] = Field([], description="발견한 페이지별 출처")
    context: str | None = Field(
        None, description='증거 종류와 판단 사유. 예: "푸터 제휴 배너 — 제품 미특정"'
    )
    verification_status: VerificationStatus | None = Field(
        None, description="이 항목의 신뢰도(VerificationStatus 참조)"
    )


class TreatmentPrice(BaseModel):
    """시술 가격. 원문을 text에 보존하고, 숫자는 분해해 담는다.

    실제 판매가(할인가)는 low/high에, 할인 전 정가는 regular_price에 담는다.
    """

    text: str | None = Field(
        None,
        description='가격 표기 원문 그대로. 예: "180만원", "5~30만원", "상담 후 결정"',
    )
    low: int | None = Field(
        None,
        description="실제 판매가(원화 정수). 범위 가격이면 하한(예: 5~30만원 → 50000), "
        "단일가면 그 값",
    )
    high: int | None = Field(
        None,
        description="실제 판매가의 범위 상한(예: 5~30만원 → 300000). "
        "단일가면 null 또는 low와 동일",
    )
    regular_price: int | None = Field(
        None,
        description="할인 전 정가(원화 정수). 정가와 이벤트가가 함께 표기될 때만 채운다"
        "(예: 정상가 35만 → 이벤트가 27.5만이면 regular_price=350000, low=275000)",
    )
    unit: str | None = Field(
        None, description="가격 단위. 예: 회, cc, ml, 샷, 부위, 패키지"
    )
    quantity: float | None = Field(
        None,
        description='unit의 수량. 예: "2cc 27.5만원" → unit="cc", quantity=2. '
        "단위당 단가(cc당·샷당) 계산의 근거",
    )
    currency: str | None = Field("KRW", description="통화 코드")
    include_vat: bool | None = Field(
        None, description="VAT 포함 여부. 사이트에 표기된 경우만 채운다"
    )
    is_event_price: bool | None = Field(None, description="이벤트(프로모션) 가격 여부")


class TreatmentPackage(BaseModel):
    """패키지·이벤트 구성. treatment_name이나 notes 문자열에 묻지 말고 여기에 구조화한다."""

    sessions: int | None = Field(None, description="회차 수. 예: 3회 패키지면 3")
    duration_months: int | None = Field(None, description="이용 기간(개월)")
    event_period: str | None = Field(
        None, description='이벤트 기간. 예: "2026-05-01 ~ 2026-05-31"'
    )


class TreatmentInfo(BaseModel):
    """시술 1건. 시술명과 거기 연결된 제품·장비·가격을 담는다."""

    treatment_name: str = Field(
        description="홈페이지 표기 시술명(병원 자체 브랜드명 포함)"
    )
    category: str | None = Field(None, description="시술 분류. 예: 리프팅, 보톡스")
    product_name: str | None = Field(
        None,
        description='이 시술에 쓰이는 제품의 매칭 확정 이름. 복수면 "; "로 연결',
    )
    equipment_name: str | None = Field(
        None,
        description='이 시술에 쓰이는 장비의 매칭 확정 이름. 복수면 "; "로 연결',
    )
    price: TreatmentPrice | None = Field(None, description="가격(원문+분해값)")
    package: TreatmentPackage | None = Field(None, description="패키지·이벤트 구성")
    notes: str | None = Field(None, description="비고(부작용·조건 등 기타 정보)")
    source_page: str | None = Field(
        None, description="이 시술 정보를 수집한 페이지의 실제 URL"
    )
    verification_status: VerificationStatus | None = Field(
        None, description="이 항목의 신뢰도(VerificationStatus 참조)"
    )


class LanguageSupport(BaseModel):
    """다국어(외국인 환자) 지원 현황."""

    supported_languages: list[str] = Field(
        [], description="홈페이지가 지원하는 언어 목록"
    )
    has_language_switcher: bool = Field(False, description="언어 전환 UI 존재 여부")
    foreign_language_pages: dict[str, str] = Field(
        {},
        description="언어별 페이지 URL(언어 → URL). "
        "스위처가 있으면 각 언어를 눌러 URL까지 채운다(존재 표시만 하고 비우지 말 것)",
    )


class Address(BaseModel):
    """병원 주소. 입력 병원 정보 또는 홈페이지에서 확인한 주소를 구조화해 보존한다.

    좌표·시군구까지 담아 두면 DB 적재 시 재조인이 필요 없다.
    """

    sido: str | None = Field(None, description='시도. 예: "서울"')
    sggu: str | None = Field(None, description='시군구. 예: "강남구"')
    emdong: str | None = Field(None, description='읍면동. 예: "역삼동"')
    road_address: str | None = Field(
        None, description="도로명 주소 전체(건물명·층 등 상세 포함)"
    )
    latitude: float | None = Field(None, description="위도(확인된 경우만)")
    longitude: float | None = Field(None, description="경도(확인된 경우만)")


class SiblingBranch(BaseModel):
    """같은 네트워크·그룹의 다른 지점."""

    name: str = Field(description="지점 이름")
    url: str | None = Field(None, description="지점 홈페이지 URL(별도 도메인 포함)")
    address: str | None = Field(None, description="지점 주소")


class Branches(BaseModel):
    """단독/지점/본점 구분과 분원 정보. 별도 도메인으로 운영되는 분원도 여기 기록한다."""

    type: Literal["single", "branch", "headquarters"] | None = Field(
        None, description="single=단독 의원, branch=지점, headquarters=본점"
    )
    network_group: str | None = Field(None, description="네트워크·그룹 이름")
    this_branch_name: str | None = Field(
        None, description="이 홈페이지가 다루는 지점의 이름"
    )
    sibling_branches: list[SiblingBranch] = Field([], description="발견한 다른 지점들")


class CrawlCompleteness(BaseModel):
    """영역별 수집 완료 체크리스트(SKILL.md §7). 해당 영역을 수집했으면 true."""

    operation_info: bool = False
    doctors: bool = False
    products: bool = False
    equipments: bool = False
    treatments: bool = False
    prices: bool = False
    language_support: bool = False


class CrawlCost(BaseModel):
    """크롤링에 든 비용. 실측값만 적는다 — 모르는 값은 null로 둔다(추정해 채우지 말 것).

    토큰·비용 실측은 보통 바깥(배치 러너의 API usage)에서만 가능하다.
    대화형 실행에서는 model·duration_seconds 정도만 채워질 수 있다.
    """

    model: str | None = Field(None, description='사용한 모델 ID. 예: "claude-opus-4-8"')
    effort: str | None = Field(
        None,
        description='추론 effort. 예: "low"/"medium"/"high". 같은 비용도 effort에 '
        "따라 품질이 달라지므로 비용과 함께 기록한다(러너가 채움).",
    )
    input_tokens: int | None = Field(None, description="입력 토큰 합계")
    output_tokens: int | None = Field(None, description="출력 토큰 합계")
    cost_usd: float | None = Field(None, description="비용(USD)")
    duration_seconds: int | None = Field(
        None, description="크롤링 시작~완료 소요 시간(초)"
    )


class FollowUpItem(BaseModel):
    """시간·예산·접근성 때문에 못 끝낸 영역 1건. 후속 실행이 이어받을 수 있게 구조화한다.

    notes(서술)·completeness(true/false)와 별개로, "무엇을 더 하면 완료되는지"를
    재실행 가능한 형태로 남기는 행동 지향 항목이다.
    """

    area: str = Field(
        description='미진한 영역. 예: "equipments", "products", "doctors", "prices", '
        '"treatments", "operation_info"'
    )
    reason: Literal[
        "budget_time", "image_unreadable", "blocked", "needs_deeper_crawl", "other"
    ] = Field(
        description="중단 사유. budget_time=예산·시간 한도, image_unreadable=이미지가 흐림·잘림으로 판독 불가, "
        "blocked=접속·로그인·차단, needs_deeper_crawl=구조상 더 들어가야 함(미방문 탭·상세 등)"
    )
    detail: str = Field(
        description="무엇이 미수집이고 무엇을 하면 완료되는지(행동 지향). "
        '예: "시술안내 14개 탭 중 8개 미방문 — 해당 탭 방문 시 제품 추가 확보 가능"'
    )
    urls: list[str] = Field(
        [], description="후속 실행이 재방문할 페이지·이미지 URL(있으면)"
    )


class CrawlMetadata(BaseModel):
    """크롤링 과정 기록. 무엇을 방문했고 어떤 판단을 했는지 남긴다."""

    pages_crawled: int = Field(0, description="방문한 페이지 수")
    pages_visited: list[str] = Field(
        [], description="실제 방문한 URL 전부(시술 상세 페이지 포함)"
    )
    crawl_method: str | None = Field(
        None,
        description='추출 방식 한 줄 설명. 예: "Playwright MCP 직접 방문 + 이미지 가격 vision 판독"',
    )
    errors: list[str] = Field([], description="접속 실패·차단 등 오류 기록")
    completeness: CrawlCompleteness = Field(
        CrawlCompleteness(), description="영역별 수집 완료 체크리스트"
    )
    follow_up: list[FollowUpItem] = Field(
        [],
        description="시간·예산·접근성으로 못 끝낸 영역 — 후속 실행용. "
        "다 끝냈으면 빈 리스트. completeness가 false인 영역이 있으면 보통 여기에 사유·재방문 URL을 남긴다",
    )
    cost: CrawlCost | None = Field(
        None, description="크롤링에 든 비용(모델·토큰·USD·소요 시간)"
    )
    notes: list[str] = Field(
        [],
        description="판단 근거 모음 — 가격 출처(텍스트/이미지/없음), 매칭·보류 사유, "
        "별도 도메인·다국어 등 정책 결정이 필요한 사항, 미방문 영역, "
        "규칙과 다르게 판단한 근거. 비워두지 않는다(SKILL.md §7)",
    )


class HospitalHomepageResult(BaseModel):
    """홈페이지 추출 최종 결과(출력 JSON의 루트).

    output/{병원ID}_{병원이름}_homepage.json으로 저장한다.
    """

    hospital_id: str = Field(
        description="입력받은 병원DB 아이디. 없으면 도메인 기반 임시 ID(notes에 명시)"
    )
    hospital_name: str = Field(
        description="입력받은 병원이름. 없으면 홈페이지 표기에서 추출"
    )
    crawled_at: str = Field(
        description='수집 시각. ISO 8601. 예: "2026-01-01T00:00:00+09:00"'
    )
    address: Address | None = Field(None, description="병원 주소(구조화)")
    homepage_url: str | None = Field(
        None, description="실제 사용한 최종 URL(리다이렉트됐으면 도착한 URL)"
    )
    identity_status: Literal["match", "mismatch", "partial"] | None = Field(
        None,
        description="입력 병원 정보와 홈페이지 표기(병원명·대표·사업자번호·주소)의 대조 결과. "
        "입력 병원 정보가 없으면 null",
    )
    alternative_urls: list[str] = Field(
        [], description="크롤링 중 발견한 다른 URL(언어 버전·구도메인 등)"
    )

    branches: Branches | None = Field(None, description="단독/지점/본점·분원 정보")
    operation_info: OperationInfo | None = Field(None, description="운영정보")
    doctors: list[DoctorInfo] = Field([], description="의료진 목록")
    products: ProductsInfo = Field(ProductsInfo(), description="취급제품(매칭+보류)")
    equipments: EquipmentsInfo = Field(
        EquipmentsInfo(), description="취급장비(매칭+보류)"
    )
    manufacturer_signals: list[ManufacturerSignal] = Field(
        [], description="제품 미특정 제조사 수준 거래 신호(제휴 배너 등)"
    )
    treatments: list[TreatmentInfo] = Field([], description="시술 목록(가격 포함)")
    language_support: LanguageSupport = Field(
        LanguageSupport(), description="다국어 지원 현황"
    )
    crawl_metadata: CrawlMetadata = Field(
        CrawlMetadata(), description="크롤링 과정 기록"
    )


def _nav(node, loc):
    """loc 경로의 노드를 반환(없으면 None)."""
    cur = node
    for k in loc:
        if isinstance(cur, dict) and not isinstance(k, int):
            cur = cur.get(k)
        elif isinstance(cur, list) and isinstance(k, int) and 0 <= k < len(cur):
            cur = cur[k]
        else:
            return None
    return cur


_MISSING = object()


def _skeleton_value(skeleton, loc):
    cur = skeleton
    for k in loc:
        if isinstance(cur, dict) and not isinstance(k, int) and k in cur:
            cur = cur[k]
        else:
            return _MISSING
    return cur


def _fix_at(data, skeleton, loc):
    """무효한 loc를 고친다: 리스트를 지나면 그 항목을 버리고,
    딕셔너리 경로면 스켈레톤 기본값으로 복원하거나 그 키를 지운다."""
    for i, k in enumerate(loc):
        if isinstance(k, int):  # 리스트 항목이 문제 → 그 항목만 제거(나머지는 보존)
            parent = _nav(data, loc[:i])
            if isinstance(parent, list) and 0 <= k < len(parent):
                del parent[k]
                return True
            return False
    parent = _nav(data, loc[:-1])
    if not isinstance(parent, dict):
        return False
    last = loc[-1]
    sv = _skeleton_value(skeleton, loc)
    if sv is not _MISSING:  # 필수 필드 → 스켈레톤 기본값으로 복원
        parent[last] = sv
        return True
    if last in parent:  # 선택 필드 → 제거(기본값이 채워짐)
        del parent[last]
        return True
    return False


def _empty(v):
    """None·빈문자열·빈리스트·빈딕트를 '값 없음'으로 본다."""
    return v is None or v == "" or v == [] or v == {}


# 에이전트가 operation_info 자리에 자주 쓰는 별칭 필드명 → 스키마 필드명.
_OPERATION_ALIASES = {
    "registration_number": "business_number",
    "business_registration_number": "business_number",
    "biz_number": "business_number",
    "representative": "representative_name",
    "representative_director": "representative_name",
    "ceo": "representative_name",
    "tel": "phone",
    "phone_number": "phone",
    "hours": "operating_hours",
    "hours_note": "operating_hours_note",
}

# unmatched 제품/장비 항목에서 raw_name 대신 쓰이는 이름 키들(우선순위 순).
_NAME_ALIASES = (
    "raw_name",
    "name",
    "product_name",
    "equipment_name",
    "product",
    "equipment",
    "title",
    "label",
)

# language_support의 supported_languages 자리에 쓰이는 별칭 키들.
_LANGUAGE_ALIASES = (
    "languages_supported",
    "languages",
    "language",
    "supported_languages",
)


def _as_unmatched(it):
    """제품/장비 보류 항목을 raw_name 키를 갖도록 정규화한다. 만들 수 없으면 None."""
    if isinstance(it, str):
        return {"raw_name": it.strip()} if it.strip() else None
    if not isinstance(it, dict):
        return None
    if it.get("raw_name"):
        return it
    for k in _NAME_ALIASES:
        if it.get(k):
            return {**it, "raw_name": it[k]}
    return None


def _normalize_pe(raw, kind, matched_key, unmatched_key, required):
    """제품/장비 블록을 스키마 구조(`{kind: {matched, unmatched}}`)로 모은다.

    에이전트가 최상위에 둔 matched_*/unmatched_*와, raw[kind]가 dict든 list든 모두 합친다.
    매칭 정식명(required: product_kr/name_kr)이 없는 matched 항목은 거짓 매칭을 막기 위해
    버리지 않고 unmatched로 내린다(이름은 보존).
    """
    node = raw.get(kind)
    block = node if isinstance(node, dict) else {}
    extra_list = node if isinstance(node, list) else []
    matched_src = (block.get(matched_key) or []) + (raw.pop(matched_key, None) or [])
    unmatched_src = (
        (block.get(unmatched_key) or [])
        + (raw.pop(unmatched_key, None) or [])
        + extra_list
    )

    matched, demoted = [], []
    for it in matched_src:
        if isinstance(it, dict) and it.get(required):
            matched.append(it)
        else:  # 정식명 없는 matched → 거짓매칭 방지 위해 unmatched로 살려 내린다
            u = _as_unmatched(it)
            if u:
                demoted.append(u)
    unmatched = [u for u in (_as_unmatched(x) for x in unmatched_src) if u] + demoted
    if matched or unmatched:
        raw[kind] = {matched_key: matched, unmatched_key: unmatched}


def _normalize_operation(raw):
    """operation_info를 스키마 구조로 모은다. business_info 별칭과 키 별칭을 흡수한다."""
    op = (
        raw.get("operation_info") if isinstance(raw.get("operation_info"), dict) else {}
    )
    bi = raw.pop("business_info", None)
    merged = {}
    for src in [op, bi if isinstance(bi, dict) else {}]:  # op(정식)이 먼저 → 우선
        for k, v in src.items():
            key = _OPERATION_ALIASES.get(k, k)
            if not _empty(v) and _empty(merged.get(key)):
                merged[key] = v
    # 주소는 operation_info 스키마에 없으니 최상위 address로 옮긴다(빈 경우만).
    addr = merged.pop("address", None)
    if addr and _empty(raw.get("address")):
        raw["address"] = {"road_address": addr} if isinstance(addr, str) else addr
    if merged:
        raw["operation_info"] = merged


def _normalize_language(raw):
    """language_support를 `{supported_languages: [...]}` 구조로 모은다."""
    node = raw.get("language_support")
    ls = (
        node
        if isinstance(node, dict)
        else {"supported_languages": node}
        if isinstance(node, list)
        else {}
    )
    if _empty(ls.get("supported_languages")):
        for alias in _LANGUAGE_ALIASES:
            v = raw.get(alias)
            if isinstance(v, str):
                v = [v]
            if v:
                ls["supported_languages"] = v
                break
    for alias in _LANGUAGE_ALIASES:
        raw.pop(alias, None)
    if ls:
        raw["language_support"] = ls


def _normalize_metadata(raw):
    """최상위에 흩어진 크롤 메타(crawl_method·pages_visited 등)를 crawl_metadata로 모은다."""
    cm = (
        raw.get("crawl_metadata") if isinstance(raw.get("crawl_metadata"), dict) else {}
    )
    for top in (
        "crawl_method",
        "pages_visited",
        "pages_crawled",
        "follow_up",
        "errors",
    ):
        if top in raw:
            if _empty(cm.get(top)):
                cm[top] = raw[top]
            raw.pop(top, None)
    if isinstance(raw.get("notes"), list) and _empty(cm.get("notes")):
        cm["notes"] = raw.pop("notes")
    if cm:
        raw["crawl_metadata"] = cm


def normalize_aliases(raw):
    """에이전트가 스키마와 다른 흔한 필드명·위치로 쓴 데이터를 스키마 구조로 옮긴다.

    repair_to_valid는 스키마 밖 최상위 키와 필수 필드가 빠진 항목을 통째로 버리므로, 이
    패스가 없으면 거둔 데이터가 조용히 사라진다 — 저비용 모델(Sonnet/low)이 흔히
    business_info(↔operation_info)·staff(↔doctors)·최상위 matched_products(↔products 중첩)·
    unmatched 항목 {"name":..}(↔{"raw_name":..})·languages_supported(↔language_support)·
    최상위 crawl_method 등으로 쓴다(실측: 기본 설정 sample10 10/10 EMPTY의 직접 원인).
    보수적으로: 정식 필드가 이미 차 있으면 덮지 않고 빈 칸만 채우며, 매칭 근거 없는 항목을
    matched로 올리지 않는다(거짓 매칭 방지). aggregate_from_treatments와 같은 무손실 철학.
    """
    if not isinstance(raw, dict):
        return raw
    raw = copy.deepcopy(raw)

    _normalize_pe(
        raw, "products", "matched_products", "unmatched_products", "product_kr"
    )
    _normalize_pe(
        raw, "equipments", "matched_equipments", "unmatched_equipments", "name_kr"
    )
    _normalize_operation(raw)
    _normalize_language(raw)
    _normalize_metadata(raw)

    if _empty(raw.get("doctors")) and raw.get("staff"):
        raw["doctors"] = raw.get("staff")
    raw.pop("staff", None)
    for d in raw.get("doctors") or []:
        if isinstance(d, dict) and not d.get("name"):
            for k in ("doctor_name", "full_name"):
                if d.get(k):
                    d["name"] = d[k]
                    break

    if isinstance(raw.get("address"), str):
        raw["address"] = {"road_address": raw["address"]}

    for t in raw.get("treatments") or []:
        if not isinstance(t, dict):
            continue
        if not t.get("treatment_name"):
            for k in ("name", "title", "treatment", "treatment_kr"):
                if t.get(k):
                    t["treatment_name"] = t[k]
                    break
        p = t.get("price")
        if isinstance(p, str) and p.strip():
            t["price"] = {"text": p.strip()}
        elif isinstance(p, (int, float)) and not isinstance(p, bool):
            t["price"] = {"low": int(p), "text": str(p)}

    return raw


def repair_to_valid(raw, skeleton):
    """raw를 스켈레톤 위에 얹고 무효 부분만 떨궈 스키마를 통과하는 dict로 만든다.

    먼저 normalize_aliases로 별칭 필드명·위치(business_info·staff·최상위 matched_* 등)를
    스키마 구조로 옮긴 뒤(버리기 전에 살린다), 유효 데이터는 최대한 보존하고, 시술에 거둔
    제품·장비 이름은 aggregate_from_treatments로 최상위 unmatched에 보강한다(무손실 — 이
    보장을 함수 안에 둬 모든 호출자가 받게 한다). 반환값은 항상 스키마에 맞는다(보장).
    """
    raw = normalize_aliases(raw)
    data = {**skeleton, **{k: v for k, v in raw.items() if k in skeleton}}
    result = None
    for _ in range(500):
        try:
            result = HospitalHomepageResult.model_validate(data).model_dump(mode="json")
            break
        except ValidationError as e:
            fixed = False
            for err in e.errors():
                if _fix_at(data, skeleton, list(err["loc"])):
                    fixed = True
            if not fixed:
                break
    if result is None:  # 최후: 스켈레톤만이라도 유효하게
        result = HospitalHomepageResult.model_validate(skeleton).model_dump(mode="json")
    # 거둔 제품·장비를 잃지 않게 시술에서 자동 집계한 뒤 재검증한다.
    aggregate_from_treatments(result)
    return HospitalHomepageResult.model_validate(result).model_dump(mode="json")


def _norm(s):
    """비교용 정규화: NFKC + 공백 제거 + 소문자."""
    return unicodedata.normalize("NFKC", (s or "").strip()).casefold()


def _split_names(s):
    """'온다리프팅, 슈링크 유니버스' 처럼 ,·; 로 묶인 이름을 분해한다."""
    if not s:
        return []
    return [p.strip() for p in s.replace(";", ",").split(",") if p.strip()]


def aggregate_from_treatments(data):
    """시술(treatments)에 박힌 제품·장비 이름이 products/equipments에서 누락되지 않게 보강한다.

    이 프로젝트의 1순위 가치 출력은 취급 제품·장비다. 에이전트가 그 이름을
    treatments[].product_name/equipment_name에는 잘 적고도 최상위 products/equipments로
    집계하지 못하거나(혹은 잘못된 필드명으로 써 repair가 떨궈) 통째로 잃는 일이 있었다
    (실측: 톡스앤필 — 시술 61개에 제품17·장비13이 박혔는데 products/equipments는 0).
    그래서 러너가 결정론적으로 보강한다: 시술에 나온 이름이 matched/unmatched 어디에도
    없으면 unmatched에 집계 항목으로 넣어 **거둔 이름을 절대 잃지 않게** 한다.
    카탈로그 매칭(unmatched→matched)은 여기서 하지 않는다(에이전트/별도 패스의 일).
    반환값: (추가한 제품 수, 추가한 장비 수).
    """
    treatments = data.get("treatments") or []
    products = data.setdefault("products", {})
    equipments = data.setdefault("equipments", {})

    def _aggregate(matched, matched_fields, unmatched, tx_field, model):
        # 이미 matched(정식명)·unmatched(원표기)에 잡힌 이름 — 중복 추가 방지용 집합.
        seen = {
            _norm(it[f])
            for it in matched
            if isinstance(it, dict)
            for f in matched_fields
            if it.get(f)
        }
        seen |= {
            _norm(it["raw_name"])
            for it in unmatched
            if isinstance(it, dict) and it.get("raw_name")
        }
        # 시술에서 이름 → (원표기, 언급횟수, 출처 URL 집합) 수집.
        collected = {}
        for t in treatments:
            if not isinstance(t, dict):
                continue
            src = t.get("source_page")
            for raw in _split_names(t.get(tx_field)):
                e = collected.setdefault(_norm(raw), [raw, 0, set()])
                e[1] += 1
                if src:
                    e[2].add(src)
        added = 0
        for key, (raw, cnt, srcs) in collected.items():
            if key in seen:
                continue
            # 스키마 모델로 만들어 필드명·기본값(SourceRef.channel 등)을 모델에서 끌어온다
            # — 스키마 필드명이 바뀌어도 조용히 데이터를 잃지 않게.
            unmatched.append(
                model(
                    raw_name=raw,
                    mention_count=cnt,
                    sources=[SourceRef(url=u) for u in sorted(srcs)],
                    context="시술(treatments)에서 자동 집계 — 카탈로그 매칭 미수행",
                ).model_dump(mode="json")
            )
            seen.add(key)
            added += 1
        return added

    return (
        _aggregate(
            products.get("matched_products") or [],
            ("product_kr", "brand_kr"),
            products.setdefault("unmatched_products", []),
            "product_name",
            UnmatchedProduct,
        ),
        _aggregate(
            equipments.get("matched_equipments") or [],
            ("name_kr", "name_en"),
            equipments.setdefault("unmatched_equipments", []),
            "equipment_name",
            UnmatchedEquipment,
        ),
    )


def _has_useful_data(d):
    p = d.get("products") or {}
    e = d.get("equipments") or {}
    return bool(
        p.get("matched_products")
        or p.get("unmatched_products")
        or e.get("matched_equipments")
        or e.get("unmatched_equipments")
        or d.get("treatments")
        or d.get("doctors")
        or d.get("operation_info")
    )


if __name__ == "__main__":
    # 복구 모드: python output_scheme.py --repair <path> --id X --name Y --url Z
    # 에이전트가 쓴 (무효일 수 있는) JSON을 스키마 통과 형태로 고쳐 덮어쓴다.
    # 파일이 없거나 깨졌으면 최소 유효 스켈레톤을 쓴다. 러너가 검증 대신 호출한다.
    if "--repair" in sys.argv:
        a = sys.argv
        path = a[a.index("--repair") + 1]

        def _arg(name, default=None):
            return a[a.index(name) + 1] if name in a else default

        skeleton = HospitalHomepageResult(
            hospital_id=_arg("--id", "unknown"),
            hospital_name=_arg("--name", "unknown"),
            homepage_url=_arg("--url"),
            # 필수 필드의 유효 placeholder. raw나 러너 백필이 실제값으로 덮는다.
            crawled_at="1970-01-01T00:00:00+09:00",
        ).model_dump(mode="json")
        try:
            raw = json.load(open(path, encoding="utf-8"))
            if not isinstance(raw, dict):
                raw = {}
        except (FileNotFoundError, json.JSONDecodeError):
            raw = {}
        fixed = repair_to_valid(
            raw, skeleton
        )  # 유효화 + 시술→제품/장비 무손실 집계 포함
        with open(path, "w", encoding="utf-8") as f:
            json.dump(fixed, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("USEFUL" if _has_useful_data(fixed) else "EMPTY", path)
    else:
        for path in sys.argv[1:]:
            HospitalHomepageResult.model_validate(
                json.load(open(path, encoding="utf-8"))
            )
            print("OK", path)
