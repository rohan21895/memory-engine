use std::{
    fs,
    path::{Path, PathBuf},
    sync::Arc,
};

use serde::Serialize;
use tokio::sync::Mutex;
use tonic::transport::{Channel, Endpoint, Uri};

pub mod proto {
    tonic::include_proto!("memory_engine.media_query.v1");
}

use proto::{
    media_query_client::MediaQueryClient, Cursor, GetProxyBytesRequest, LibraryStatsRequest,
    ListMediaRequest, ListPeopleRequest, ReviewQueueRequest, SearchMediaRequest, SortOrder,
};

const MAX_PAGE_SIZE: i32 = 240;

#[derive(Clone)]
pub struct MediaQueryGateway {
    endpoint: Endpoint,
    client: Arc<Mutex<Option<MediaQueryClient<Channel>>>>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LibraryItem {
    pub media_id: String,
    pub kind: String,
    pub thumbnail_proxy_id: Option<String>,
    pub width: i32,
    pub height: i32,
    pub captured_unix: Option<i64>,
    pub capture_precision: String,
    pub quality: Option<f32>,
    pub quality_is_comparable: bool,
    pub favorite: bool,
    pub rejected: bool,
    pub sensitive: bool,
    pub safety_unknown: bool,
    pub person_ids: Vec<String>,
    pub span_id: Option<String>,
    pub duration_ms: Option<i64>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LibraryPage {
    pub items: Vec<LibraryItem>,
    pub total: i64,
    pub next_cursor: Option<String>,
    pub has_more: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProxyAsset {
    pub path: String,
    pub media_type: String,
    pub blake3: String,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Person {
    pub person_id: String,
    pub display_name: String,
    pub confirmed_face_count: i64,
    pub cover_proxy_id: Option<String>,
    pub is_minor: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FaceBox {
    pub x: f32,
    pub y: f32,
    pub w: f32,
    pub h: f32,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewItem {
    pub face_id: String,
    pub media_id: String,
    pub proxy_id: Option<String>,
    pub face_box: Option<FaceBox>,
    pub candidate_person_id: Option<String>,
    pub similarity: f32,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PeoplePage {
    pub people: Vec<Person>,
    pub review_items: Vec<ReviewItem>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LibraryStats {
    pub media_count: i64,
    pub proxy_count: i64,
    pub person_count: i64,
    pub review_queue_depth: i64,
    pub media_awaiting_analysis: i64,
    pub media_awaiting_proxies: i64,
}

impl MediaQueryGateway {
    pub fn new(endpoint: &str) -> Result<Self, String> {
        let endpoint = Endpoint::from_shared(endpoint.to_owned())
            .map_err(|_| "The local library service address is invalid.".to_owned())?;
        assert_loopback(endpoint.uri())?;
        Ok(Self {
            endpoint: endpoint.connect_timeout(std::time::Duration::from_secs(3)),
            client: Arc::new(Mutex::new(None)),
        })
    }

    async fn client(&self) -> Result<MediaQueryClient<Channel>, String> {
        let mut cached = self.client.lock().await;
        if let Some(client) = cached.as_ref() {
            return Ok(client.clone());
        }
        let channel = self.endpoint.connect().await.map_err(|_| unavailable())?;
        let client = MediaQueryClient::new(channel);
        *cached = Some(client.clone());
        Ok(client)
    }

    pub async fn list_media(
        &self,
        query: &str,
        cursor: Option<String>,
        limit: usize,
    ) -> Result<LibraryPage, String> {
        let cursor = cursor
            .filter(|token| !token.is_empty())
            .map(|token| Cursor { token });
        let limit = i32::try_from(limit)
            .unwrap_or(MAX_PAGE_SIZE)
            .clamp(1, MAX_PAGE_SIZE);
        let mut client = self.client().await?;
        let response = if query.trim().is_empty() {
            client
                .list_media(ListMediaRequest {
                    limit,
                    cursor,
                    person_ids: Vec::new(),
                    captured_between: None,
                    media_kinds: Vec::new(),
                    include_sensitive: false,
                    include_rejected: false,
                    order: SortOrder::CapturedDesc as i32,
                })
                .await
        } else {
            client
                .search_media(SearchMediaRequest {
                    query: query.trim().to_owned(),
                    limit,
                    cursor,
                    include_sensitive: false,
                    include_rejected: false,
                })
                .await
        }
        .map_err(status_message)?
        .into_inner();
        let page = response.page.unwrap_or_default();
        Ok(LibraryPage {
            items: response.items.into_iter().map(LibraryItem::from).collect(),
            total: response.total,
            next_cursor: page
                .next
                .map(|next| next.token)
                .filter(|token| !token.is_empty()),
            has_more: page.has_more,
        })
    }

    pub async fn proxy_bytes(&self, proxy_id: &str) -> Result<(Vec<u8>, String, String), String> {
        if !is_blake3(proxy_id) {
            return Err("That preview reference is invalid.".to_owned());
        }
        let mut client = self.client().await?;
        let response = client
            .get_proxy_bytes(GetProxyBytesRequest {
                proxy_id: proxy_id.to_owned(),
            })
            .await
            .map_err(status_message)?
            .into_inner();
        if !is_blake3(&response.blake3)
            || blake3::hash(&response.data).to_hex().as_str() != response.blake3
        {
            return Err(
                "That preview failed its integrity check. It was not displayed.".to_owned(),
            );
        }
        Ok((response.data, response.media_type, response.blake3))
    }

    pub async fn people_page(&self, review_limit: usize) -> Result<PeoplePage, String> {
        let mut client = self.client().await?;
        let people = client
            .list_people(ListPeopleRequest {
                include_unnamed_clusters: true,
            })
            .await
            .map_err(status_message)?
            .into_inner()
            .people
            .into_iter()
            .map(|person| Person {
                person_id: person.person_id,
                display_name: person.display_name,
                confirmed_face_count: person.confirmed_face_count,
                cover_proxy_id: nonempty(person.cover_proxy_id),
                is_minor: person.is_minor,
            })
            .collect();
        let review_items = client
            .review_queue(ReviewQueueRequest {
                limit: i32::try_from(review_limit)
                    .unwrap_or(MAX_PAGE_SIZE)
                    .clamp(1, MAX_PAGE_SIZE),
            })
            .await
            .map_err(status_message)?
            .into_inner()
            .items
            .into_iter()
            .map(|item| ReviewItem {
                face_id: item.face_id,
                media_id: item.media_id,
                proxy_id: nonempty(item.proxy_id),
                face_box: item.r#box.map(|face| FaceBox {
                    x: face.x,
                    y: face.y,
                    w: face.w,
                    h: face.h,
                }),
                candidate_person_id: nonempty(item.candidate_person_id),
                similarity: item.similarity,
            })
            .collect();
        Ok(PeoplePage {
            people,
            review_items,
        })
    }

    pub async fn stats(&self) -> Result<LibraryStats, String> {
        let mut client = self.client().await?;
        let stats = client
            .library_stats(LibraryStatsRequest {})
            .await
            .map_err(status_message)?
            .into_inner();
        Ok(LibraryStats {
            media_count: stats.media_count,
            proxy_count: stats.proxy_count,
            person_count: stats.person_count,
            review_queue_depth: stats.review_queue_depth,
            media_awaiting_analysis: stats.media_awaiting_analysis,
            media_awaiting_proxies: stats.media_awaiting_proxies,
        })
    }
}

impl From<proto::MediaSummary> for LibraryItem {
    fn from(item: proto::MediaSummary) -> Self {
        Self {
            media_id: item.media_id,
            kind: item.kind,
            thumbnail_proxy_id: nonempty(item.thumbnail_proxy_id),
            width: item.oriented_width_px,
            height: item.oriented_height_px,
            captured_unix: item.captured_unix,
            capture_precision: item.capture_precision,
            quality: item.quality,
            quality_is_comparable: item.quality_is_comparable,
            favorite: item.is_favorite,
            rejected: item.is_rejected,
            sensitive: item.is_sensitive,
            safety_unknown: item.safety_unknown,
            person_ids: item.person_ids,
            span_id: item.span_id,
            duration_ms: item.duration_ms,
        }
    }
}

pub fn cache_proxy(
    cache_directory: &Path,
    data: &[u8],
    media_type: &str,
    digest: &str,
) -> Result<ProxyAsset, String> {
    let extension = match media_type {
        "image/jpeg" => "jpg",
        "image/png" => "png",
        "image/webp" => "webp",
        "image/avif" => "avif",
        _ => return Err("That preview uses a format Photeo cannot display yet.".to_owned()),
    };
    if !is_blake3(digest) || blake3::hash(data).to_hex().as_str() != digest {
        return Err("That preview failed its integrity check. It was not cached.".to_owned());
    }
    fs::create_dir_all(cache_directory)
        .map_err(|_| "Photeo could not prepare its private preview cache.".to_owned())?;
    let destination = cache_directory.join(format!("{digest}.{extension}"));
    if destination.exists() {
        let existing = fs::read(&destination)
            .map_err(|_| "Photeo could not verify its private preview cache.".to_owned())?;
        if blake3::hash(&existing).to_hex().as_str() != digest {
            return Err("A cached preview failed its integrity check.".to_owned());
        }
    } else {
        let partial = cache_directory.join(format!(".{digest}.partial"));
        fs::write(&partial, data)
            .map_err(|_| "Photeo could not save a private preview.".to_owned())?;
        match fs::rename(&partial, &destination) {
            Ok(()) => {}
            Err(_) if destination.exists() => {
                let _ = fs::remove_file(&partial);
            }
            Err(_) => return Err("Photeo could not publish a private preview.".to_owned()),
        }
    }
    let published = fs::read(&destination)
        .map_err(|_| "Photeo could not verify its private preview cache.".to_owned())?;
    if blake3::hash(&published).to_hex().as_str() != digest {
        return Err("A cached preview failed its integrity check.".to_owned());
    }
    Ok(ProxyAsset {
        path: destination.to_string_lossy().into_owned(),
        media_type: media_type.to_owned(),
        blake3: digest.to_owned(),
    })
}

pub fn proxy_cache_directory(root: &Path) -> PathBuf {
    root.join("proxy-cache")
}

fn assert_loopback(uri: &Uri) -> Result<(), String> {
    let scheme_ok = uri.scheme_str() == Some("http");
    let host_ok = matches!(uri.host(), Some("127.0.0.1" | "::1" | "[::1]"));
    let port_ok = uri.port_u16().is_some_and(|port| port > 0);
    let path_ok = uri.path().is_empty() || uri.path() == "/";
    if scheme_ok && host_ok && port_ok && path_ok && uri.query().is_none() {
        Ok(())
    } else {
        Err("The library service must use a private loopback address.".to_owned())
    }
}

fn unavailable() -> String {
    "Photeo's local library index is not ready. Your originals and scan progress are safe."
        .to_owned()
}

fn status_message(status: tonic::Status) -> String {
    match status.code() {
        tonic::Code::Unavailable | tonic::Code::DeadlineExceeded => unavailable(),
        tonic::Code::InvalidArgument => {
            "Photeo could not understand that library request. Try a broader search.".to_owned()
        }
        tonic::Code::NotFound => "That private preview is not available yet.".to_owned(),
        _ => {
            "Photeo could not read the local library just now. Your originals are safe.".to_owned()
        }
    }
}

fn is_blake3(value: &str) -> bool {
    value.len() == 64
        && value
            .as_bytes()
            .iter()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(byte))
}

fn nonempty(value: String) -> Option<String> {
    (!value.is_empty()).then_some(value)
}

#[cfg(test)]
mod tests {
    use std::sync::{Arc, Mutex as StdMutex};

    use tempfile::tempdir;
    use tokio::sync::oneshot;
    use tokio_stream::wrappers::TcpListenerStream;
    use tonic::{transport::Server, Request, Response, Status};

    use super::*;
    use proto::{
        media_query_server::{MediaQuery, MediaQueryServer},
        GetMediaRequest, GetMediaResponse, GetProxyBytesResponse, LibraryStatsResponse,
        ListMediaResponse, ListPeopleResponse, MediaSummary, Page, Person as ProtoPerson,
        ReviewItem as ProtoReviewItem, ReviewQueueResponse,
    };

    const PROXY_ID: &str = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";

    #[derive(Clone, Default)]
    struct MockQuery {
        list_request: Arc<StdMutex<Option<ListMediaRequest>>>,
        search_request: Arc<StdMutex<Option<SearchMediaRequest>>>,
    }

    fn listing() -> ListMediaResponse {
        ListMediaResponse {
            items: vec![MediaSummary {
                media_id: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                    .to_owned(),
                kind: "image".to_owned(),
                thumbnail_proxy_id: PROXY_ID.to_owned(),
                oriented_width_px: 4_032,
                oriented_height_px: 3_024,
                captured_unix: Some(1_700_000_000),
                capture_precision: "second".to_owned(),
                quality: Some(0.91),
                quality_is_comparable: true,
                is_favorite: true,
                is_rejected: false,
                is_sensitive: false,
                safety_unknown: true,
                person_ids: vec!["person-one".to_owned()],
                span_id: None,
                duration_ms: None,
            }],
            page: Some(Page {
                next: Some(Cursor {
                    token: "opaque:do-not-parse".to_owned(),
                }),
                has_more: true,
            }),
            total: -1,
        }
    }

    #[tonic::async_trait]
    impl MediaQuery for MockQuery {
        async fn list_media(
            &self,
            request: Request<ListMediaRequest>,
        ) -> Result<Response<ListMediaResponse>, Status> {
            *self.list_request.lock().expect("list request lock") = Some(request.into_inner());
            Ok(Response::new(listing()))
        }

        async fn search_media(
            &self,
            request: Request<SearchMediaRequest>,
        ) -> Result<Response<ListMediaResponse>, Status> {
            *self.search_request.lock().expect("search request lock") = Some(request.into_inner());
            Ok(Response::new(listing()))
        }

        async fn get_media(
            &self,
            _request: Request<GetMediaRequest>,
        ) -> Result<Response<GetMediaResponse>, Status> {
            Ok(Response::new(GetMediaResponse::default()))
        }

        async fn get_proxy_bytes(
            &self,
            _request: Request<GetProxyBytesRequest>,
        ) -> Result<Response<GetProxyBytesResponse>, Status> {
            let data = b"verified proxy".to_vec();
            Ok(Response::new(GetProxyBytesResponse {
                blake3: blake3::hash(&data).to_hex().to_string(),
                data,
                media_type: "image/jpeg".to_owned(),
            }))
        }

        async fn list_people(
            &self,
            _request: Request<ListPeopleRequest>,
        ) -> Result<Response<ListPeopleResponse>, Status> {
            Ok(Response::new(ListPeopleResponse {
                people: vec![ProtoPerson {
                    person_id: "person-one".to_owned(),
                    display_name: "Asha".to_owned(),
                    confirmed_face_count: 42,
                    cover_proxy_id: PROXY_ID.to_owned(),
                    is_minor: false,
                }],
            }))
        }

        async fn review_queue(
            &self,
            _request: Request<ReviewQueueRequest>,
        ) -> Result<Response<ReviewQueueResponse>, Status> {
            Ok(Response::new(ReviewQueueResponse {
                items: vec![ProtoReviewItem {
                    face_id: "face-one".to_owned(),
                    media_id: "media-one".to_owned(),
                    proxy_id: PROXY_ID.to_owned(),
                    r#box: Some(proto::NormalizedBox {
                        x: 0.2,
                        y: 0.1,
                        w: 0.3,
                        h: 0.4,
                    }),
                    candidate_person_id: "person-one".to_owned(),
                    similarity: 0.82,
                }],
            }))
        }

        async fn library_stats(
            &self,
            _request: Request<LibraryStatsRequest>,
        ) -> Result<Response<LibraryStatsResponse>, Status> {
            Ok(Response::new(LibraryStatsResponse {
                media_count: 100_000,
                proxy_count: 90_000,
                person_count: 12,
                review_queue_depth: 8,
                media_awaiting_analysis: 1_200,
                media_awaiting_proxies: 320,
            }))
        }
    }

    async fn server() -> (MediaQueryGateway, MockQuery, oneshot::Sender<()>) {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("test listener");
        let address = listener.local_addr().expect("listener address");
        let mock = MockQuery::default();
        let service = mock.clone();
        let (shutdown_tx, shutdown_rx) = oneshot::channel();
        tokio::spawn(async move {
            Server::builder()
                .add_service(MediaQueryServer::new(service))
                .serve_with_incoming_shutdown(TcpListenerStream::new(listener), async {
                    let _ = shutdown_rx.await;
                })
                .await
                .expect("test media query server");
        });
        (
            MediaQueryGateway::new(&format!("http://{address}")).expect("loopback gateway"),
            mock,
            shutdown_tx,
        )
    }

    #[test]
    fn transport_refuses_everything_except_literal_loopback_http() {
        assert!(MediaQueryGateway::new("http://127.0.0.1:50051").is_ok());
        assert!(MediaQueryGateway::new("http://[::1]:50051").is_ok());
        for endpoint in [
            "https://127.0.0.1:50051",
            "http://localhost:50051",
            "http://0.0.0.0:50051",
            "http://192.168.1.2:50051",
            "http://127.0.0.1:50051/path",
        ] {
            assert!(
                MediaQueryGateway::new(endpoint).is_err(),
                "accepted {endpoint}"
            );
        }
    }

    #[tokio::test]
    async fn list_and_search_preserve_opaque_cursors_and_uncertainty() {
        let (gateway, mock, shutdown) = server().await;
        let page = gateway
            .list_media("", Some("caller-cursor".to_owned()), 999)
            .await
            .expect("list response");
        assert_eq!(page.total, -1);
        assert_eq!(page.next_cursor.as_deref(), Some("opaque:do-not-parse"));
        assert!(page.items[0].safety_unknown);
        assert_eq!(page.items[0].capture_precision, "second");
        let request = mock
            .list_request
            .lock()
            .expect("list request lock")
            .clone()
            .expect("list request");
        assert_eq!(request.limit, MAX_PAGE_SIZE);
        assert_eq!(request.cursor.expect("cursor").token, "caller-cursor");
        assert!(!request.include_sensitive);

        gateway
            .list_media("  beach  ", None, 20)
            .await
            .expect("search response");
        let request = mock
            .search_request
            .lock()
            .expect("search request lock")
            .clone()
            .expect("search request");
        assert_eq!(request.query, "beach");
        let _ = shutdown.send(());
    }

    #[tokio::test]
    async fn proxy_bytes_are_digest_verified_and_cached_by_content() {
        let (gateway, _, shutdown) = server().await;
        let (bytes, media_type, digest) = gateway.proxy_bytes(PROXY_ID).await.expect("proxy");
        let temporary = tempdir().expect("temporary cache");
        let first = cache_proxy(temporary.path(), &bytes, &media_type, &digest).expect("cache");
        let second = cache_proxy(temporary.path(), &bytes, &media_type, &digest).expect("replay");
        assert_eq!(first.path, second.path);
        assert_eq!(fs::read(&first.path).expect("cached bytes"), bytes);
        assert!(gateway.proxy_bytes("not-a-proxy-id").await.is_err());
        let _ = shutdown.send(());
    }

    #[tokio::test]
    async fn people_review_and_scan_stats_remain_distinct() {
        let (gateway, _, shutdown) = server().await;
        let people = gateway.people_page(12).await.expect("people");
        assert_eq!(people.people[0].display_name, "Asha");
        assert_eq!(
            people.review_items[0].candidate_person_id.as_deref(),
            Some("person-one")
        );
        assert!(people.review_items[0].face_box.is_some());
        let stats = gateway.stats().await.expect("stats");
        assert_eq!(stats.media_count, 100_000);
        assert_eq!(stats.media_awaiting_analysis, 1_200);
        assert_eq!(stats.media_awaiting_proxies, 320);
        let _ = shutdown.send(());
    }
}
