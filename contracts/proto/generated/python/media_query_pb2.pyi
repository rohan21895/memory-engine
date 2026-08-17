# GENERATED FILE -- DO NOT EDIT.
#
# Produced by contracts/proto/generate.py from contracts/proto/ml_runtime.proto
# with grpcio-tools==1.68.1. Edit the .proto and re-run `npm run codegen`.
# CI fails if these files drift from the proto.

from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SortOrder(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SORT_ORDER_UNSPECIFIED: _ClassVar[SortOrder]
    SORT_ORDER_CAPTURED_DESC: _ClassVar[SortOrder]
    SORT_ORDER_CAPTURED_ASC: _ClassVar[SortOrder]
    SORT_ORDER_QUALITY_DESC: _ClassVar[SortOrder]
    SORT_ORDER_ADDED_DESC: _ClassVar[SortOrder]
SORT_ORDER_UNSPECIFIED: SortOrder
SORT_ORDER_CAPTURED_DESC: SortOrder
SORT_ORDER_CAPTURED_ASC: SortOrder
SORT_ORDER_QUALITY_DESC: SortOrder
SORT_ORDER_ADDED_DESC: SortOrder

class Cursor(_message.Message):
    __slots__ = ("token",)
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    token: str
    def __init__(self, token: _Optional[str] = ...) -> None: ...

class Page(_message.Message):
    __slots__ = ("next", "has_more")
    NEXT_FIELD_NUMBER: _ClassVar[int]
    HAS_MORE_FIELD_NUMBER: _ClassVar[int]
    next: Cursor
    has_more: bool
    def __init__(self, next: _Optional[_Union[Cursor, _Mapping]] = ..., has_more: bool = ...) -> None: ...

class ListMediaRequest(_message.Message):
    __slots__ = ("limit", "cursor", "person_ids", "captured_between", "media_kinds", "include_sensitive", "include_rejected", "order")
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    PERSON_IDS_FIELD_NUMBER: _ClassVar[int]
    CAPTURED_BETWEEN_FIELD_NUMBER: _ClassVar[int]
    MEDIA_KINDS_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_SENSITIVE_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_REJECTED_FIELD_NUMBER: _ClassVar[int]
    ORDER_FIELD_NUMBER: _ClassVar[int]
    limit: int
    cursor: Cursor
    person_ids: _containers.RepeatedScalarFieldContainer[str]
    captured_between: TimeFilter
    media_kinds: _containers.RepeatedScalarFieldContainer[str]
    include_sensitive: bool
    include_rejected: bool
    order: SortOrder
    def __init__(self, limit: _Optional[int] = ..., cursor: _Optional[_Union[Cursor, _Mapping]] = ..., person_ids: _Optional[_Iterable[str]] = ..., captured_between: _Optional[_Union[TimeFilter, _Mapping]] = ..., media_kinds: _Optional[_Iterable[str]] = ..., include_sensitive: bool = ..., include_rejected: bool = ..., order: _Optional[_Union[SortOrder, str]] = ...) -> None: ...

class TimeFilter(_message.Message):
    __slots__ = ("start_unix", "end_unix", "include_undated")
    START_UNIX_FIELD_NUMBER: _ClassVar[int]
    END_UNIX_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_UNDATED_FIELD_NUMBER: _ClassVar[int]
    start_unix: int
    end_unix: int
    include_undated: bool
    def __init__(self, start_unix: _Optional[int] = ..., end_unix: _Optional[int] = ..., include_undated: bool = ...) -> None: ...

class ListMediaResponse(_message.Message):
    __slots__ = ("items", "page", "total")
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    PAGE_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[MediaSummary]
    page: Page
    total: int
    def __init__(self, items: _Optional[_Iterable[_Union[MediaSummary, _Mapping]]] = ..., page: _Optional[_Union[Page, _Mapping]] = ..., total: _Optional[int] = ...) -> None: ...

class MediaSummary(_message.Message):
    __slots__ = ("media_id", "kind", "thumbnail_proxy_id", "oriented_width_px", "oriented_height_px", "captured_unix", "capture_precision", "quality", "quality_is_comparable", "is_favorite", "is_rejected", "is_sensitive", "safety_unknown", "person_ids", "span_id", "duration_ms")
    MEDIA_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    THUMBNAIL_PROXY_ID_FIELD_NUMBER: _ClassVar[int]
    ORIENTED_WIDTH_PX_FIELD_NUMBER: _ClassVar[int]
    ORIENTED_HEIGHT_PX_FIELD_NUMBER: _ClassVar[int]
    CAPTURED_UNIX_FIELD_NUMBER: _ClassVar[int]
    CAPTURE_PRECISION_FIELD_NUMBER: _ClassVar[int]
    QUALITY_FIELD_NUMBER: _ClassVar[int]
    QUALITY_IS_COMPARABLE_FIELD_NUMBER: _ClassVar[int]
    IS_FAVORITE_FIELD_NUMBER: _ClassVar[int]
    IS_REJECTED_FIELD_NUMBER: _ClassVar[int]
    IS_SENSITIVE_FIELD_NUMBER: _ClassVar[int]
    SAFETY_UNKNOWN_FIELD_NUMBER: _ClassVar[int]
    PERSON_IDS_FIELD_NUMBER: _ClassVar[int]
    SPAN_ID_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    media_id: str
    kind: str
    thumbnail_proxy_id: str
    oriented_width_px: int
    oriented_height_px: int
    captured_unix: int
    capture_precision: str
    quality: float
    quality_is_comparable: bool
    is_favorite: bool
    is_rejected: bool
    is_sensitive: bool
    safety_unknown: bool
    person_ids: _containers.RepeatedScalarFieldContainer[str]
    span_id: str
    duration_ms: int
    def __init__(self, media_id: _Optional[str] = ..., kind: _Optional[str] = ..., thumbnail_proxy_id: _Optional[str] = ..., oriented_width_px: _Optional[int] = ..., oriented_height_px: _Optional[int] = ..., captured_unix: _Optional[int] = ..., capture_precision: _Optional[str] = ..., quality: _Optional[float] = ..., quality_is_comparable: bool = ..., is_favorite: bool = ..., is_rejected: bool = ..., is_sensitive: bool = ..., safety_unknown: bool = ..., person_ids: _Optional[_Iterable[str]] = ..., span_id: _Optional[str] = ..., duration_ms: _Optional[int] = ...) -> None: ...

class SearchMediaRequest(_message.Message):
    __slots__ = ("query", "limit", "cursor", "include_sensitive", "include_rejected")
    QUERY_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_SENSITIVE_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_REJECTED_FIELD_NUMBER: _ClassVar[int]
    query: str
    limit: int
    cursor: Cursor
    include_sensitive: bool
    include_rejected: bool
    def __init__(self, query: _Optional[str] = ..., limit: _Optional[int] = ..., cursor: _Optional[_Union[Cursor, _Mapping]] = ..., include_sensitive: bool = ..., include_rejected: bool = ...) -> None: ...

class GetMediaRequest(_message.Message):
    __slots__ = ("media_id",)
    MEDIA_ID_FIELD_NUMBER: _ClassVar[int]
    media_id: str
    def __init__(self, media_id: _Optional[str] = ...) -> None: ...

class GetMediaResponse(_message.Message):
    __slots__ = ("summary", "proxies", "faces", "media_record_json")
    SUMMARY_FIELD_NUMBER: _ClassVar[int]
    PROXIES_FIELD_NUMBER: _ClassVar[int]
    FACES_FIELD_NUMBER: _ClassVar[int]
    MEDIA_RECORD_JSON_FIELD_NUMBER: _ClassVar[int]
    summary: MediaSummary
    proxies: _containers.RepeatedCompositeFieldContainer[ProxyRef]
    faces: _containers.RepeatedCompositeFieldContainer[FaceRef]
    media_record_json: str
    def __init__(self, summary: _Optional[_Union[MediaSummary, _Mapping]] = ..., proxies: _Optional[_Iterable[_Union[ProxyRef, _Mapping]]] = ..., faces: _Optional[_Iterable[_Union[FaceRef, _Mapping]]] = ..., media_record_json: _Optional[str] = ...) -> None: ...

class ProxyRef(_message.Message):
    __slots__ = ("proxy_id", "kind", "width_px", "height_px", "byte_size")
    PROXY_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    WIDTH_PX_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_PX_FIELD_NUMBER: _ClassVar[int]
    BYTE_SIZE_FIELD_NUMBER: _ClassVar[int]
    proxy_id: str
    kind: str
    width_px: int
    height_px: int
    byte_size: int
    def __init__(self, proxy_id: _Optional[str] = ..., kind: _Optional[str] = ..., width_px: _Optional[int] = ..., height_px: _Optional[int] = ..., byte_size: _Optional[int] = ...) -> None: ...

class FaceRef(_message.Message):
    __slots__ = ("face_id", "person_id", "box", "detection_score", "eligible_for_automated_output")
    FACE_ID_FIELD_NUMBER: _ClassVar[int]
    PERSON_ID_FIELD_NUMBER: _ClassVar[int]
    BOX_FIELD_NUMBER: _ClassVar[int]
    DETECTION_SCORE_FIELD_NUMBER: _ClassVar[int]
    ELIGIBLE_FOR_AUTOMATED_OUTPUT_FIELD_NUMBER: _ClassVar[int]
    face_id: str
    person_id: str
    box: NormalizedBox
    detection_score: float
    eligible_for_automated_output: bool
    def __init__(self, face_id: _Optional[str] = ..., person_id: _Optional[str] = ..., box: _Optional[_Union[NormalizedBox, _Mapping]] = ..., detection_score: _Optional[float] = ..., eligible_for_automated_output: bool = ...) -> None: ...

class NormalizedBox(_message.Message):
    __slots__ = ("x", "y", "w", "h")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    W_FIELD_NUMBER: _ClassVar[int]
    H_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    w: float
    h: float
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ..., w: _Optional[float] = ..., h: _Optional[float] = ...) -> None: ...

class GetProxyBytesRequest(_message.Message):
    __slots__ = ("proxy_id",)
    PROXY_ID_FIELD_NUMBER: _ClassVar[int]
    proxy_id: str
    def __init__(self, proxy_id: _Optional[str] = ...) -> None: ...

class GetProxyBytesResponse(_message.Message):
    __slots__ = ("data", "media_type", "blake3")
    DATA_FIELD_NUMBER: _ClassVar[int]
    MEDIA_TYPE_FIELD_NUMBER: _ClassVar[int]
    BLAKE3_FIELD_NUMBER: _ClassVar[int]
    data: bytes
    media_type: str
    blake3: str
    def __init__(self, data: _Optional[bytes] = ..., media_type: _Optional[str] = ..., blake3: _Optional[str] = ...) -> None: ...

class ListPeopleRequest(_message.Message):
    __slots__ = ("include_unnamed_clusters",)
    INCLUDE_UNNAMED_CLUSTERS_FIELD_NUMBER: _ClassVar[int]
    include_unnamed_clusters: bool
    def __init__(self, include_unnamed_clusters: bool = ...) -> None: ...

class ListPeopleResponse(_message.Message):
    __slots__ = ("people",)
    PEOPLE_FIELD_NUMBER: _ClassVar[int]
    people: _containers.RepeatedCompositeFieldContainer[Person]
    def __init__(self, people: _Optional[_Iterable[_Union[Person, _Mapping]]] = ...) -> None: ...

class Person(_message.Message):
    __slots__ = ("person_id", "display_name", "confirmed_face_count", "cover_proxy_id", "is_minor")
    PERSON_ID_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    CONFIRMED_FACE_COUNT_FIELD_NUMBER: _ClassVar[int]
    COVER_PROXY_ID_FIELD_NUMBER: _ClassVar[int]
    IS_MINOR_FIELD_NUMBER: _ClassVar[int]
    person_id: str
    display_name: str
    confirmed_face_count: int
    cover_proxy_id: str
    is_minor: bool
    def __init__(self, person_id: _Optional[str] = ..., display_name: _Optional[str] = ..., confirmed_face_count: _Optional[int] = ..., cover_proxy_id: _Optional[str] = ..., is_minor: bool = ...) -> None: ...

class ReviewQueueRequest(_message.Message):
    __slots__ = ("limit",)
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    limit: int
    def __init__(self, limit: _Optional[int] = ...) -> None: ...

class ReviewQueueResponse(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[ReviewItem]
    def __init__(self, items: _Optional[_Iterable[_Union[ReviewItem, _Mapping]]] = ...) -> None: ...

class ReviewItem(_message.Message):
    __slots__ = ("face_id", "media_id", "proxy_id", "box", "candidate_person_id", "similarity")
    FACE_ID_FIELD_NUMBER: _ClassVar[int]
    MEDIA_ID_FIELD_NUMBER: _ClassVar[int]
    PROXY_ID_FIELD_NUMBER: _ClassVar[int]
    BOX_FIELD_NUMBER: _ClassVar[int]
    CANDIDATE_PERSON_ID_FIELD_NUMBER: _ClassVar[int]
    SIMILARITY_FIELD_NUMBER: _ClassVar[int]
    face_id: str
    media_id: str
    proxy_id: str
    box: NormalizedBox
    candidate_person_id: str
    similarity: float
    def __init__(self, face_id: _Optional[str] = ..., media_id: _Optional[str] = ..., proxy_id: _Optional[str] = ..., box: _Optional[_Union[NormalizedBox, _Mapping]] = ..., candidate_person_id: _Optional[str] = ..., similarity: _Optional[float] = ...) -> None: ...

class LibraryStatsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class LibraryStatsResponse(_message.Message):
    __slots__ = ("media_count", "proxy_count", "person_count", "review_queue_depth", "media_awaiting_analysis", "media_awaiting_proxies")
    MEDIA_COUNT_FIELD_NUMBER: _ClassVar[int]
    PROXY_COUNT_FIELD_NUMBER: _ClassVar[int]
    PERSON_COUNT_FIELD_NUMBER: _ClassVar[int]
    REVIEW_QUEUE_DEPTH_FIELD_NUMBER: _ClassVar[int]
    MEDIA_AWAITING_ANALYSIS_FIELD_NUMBER: _ClassVar[int]
    MEDIA_AWAITING_PROXIES_FIELD_NUMBER: _ClassVar[int]
    media_count: int
    proxy_count: int
    person_count: int
    review_queue_depth: int
    media_awaiting_analysis: int
    media_awaiting_proxies: int
    def __init__(self, media_count: _Optional[int] = ..., proxy_count: _Optional[int] = ..., person_count: _Optional[int] = ..., review_queue_depth: _Optional[int] = ..., media_awaiting_analysis: _Optional[int] = ..., media_awaiting_proxies: _Optional[int] = ...) -> None: ...
