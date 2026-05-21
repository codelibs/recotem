# recotem v1 API Overhaul Design

- **Status**: Draft (awaiting review)
- **Date**: 2026-05-21
- **Authors**: shinsuke
- **Related**:
  - downstream pilot: `recotem-playground/docs/specs/2026-05-21-smartstocknotes-reco-design.md`

## 1. Background

recotem は現在 alpha バージョンであり、HTTP API として `POST /predict/{name}` のみを提供している。本エンドポイントは「単一ユーザに対する items 推薦」(user → items, single) に固定されており、以下の機能要件が未充足:

1. **item → items 推薦** ("related items" / "similar items": 現在見ている銘柄・商品の seed リストから関連 items を返す)
2. **バルク推論** (一度のリクエストで複数 user, もしくは複数 seed set を処理)
3. **API バージョニング** (path にバージョンが含まれず、将来の非互換変更を吸収する余地がない)

alpha 段階の今こそ、これらを取り込んだ **v1 API** に一新する好機である。下流の検証案件として `smartstocknotes.jp` の関連銘柄レコメンドが控えており、`item → items` 機能を最初の駆動要件として用いる。

## 2. Goals / Non-Goals

### Goals
- HTTP API に `v1` バージョン名前空間を導入する。
- 以下4種の推論種別を、 recotem の設計思想 (「明示的・静的・診断容易」「1 recipe = 1 model = 1 endpoint group」「signed artifact + hot-swap」) と整合する形で公開する:
  - user → items (single)
  - items → items (single, "related")
  - user バルク → items
  - items バルク → items
- recipe メタデータ参照用の GET エンドポイントを公開する。
- 既存のメトリクス命名・認証・構造化エラー仕様を v1 にスライドする。

### Non-Goals
- recipe スキーマ (`docs/recipe-reference.md`) の変更。recipe 形式・artifact 形式・signing 仕様は **完全に不変**。
- 新規モデル種別 (`popularity` / `similar-users` 等) の本セッションでの実装。将来拡張点として設計には織り込むが、実装はスコープ外。
- 非同期バッチ推論 (S3 入出力のような AWS Personalize Batch Inference Jobs 相当)。同期バルクのみ対象。
- 認証方式の変更 (現行の `X-API-Key` を継続)。

## 3. Industry Survey (要約)

詳細は別途調査レポート参照。主要レコメンドAPIから抽出した業界共通パターン:

- **パスバージョニング**: Vertex AI `/v2/`、Algolia `/1/`、Spotify `/v1/`、Azure `/v1.0/` — path prefix が圧倒的多数派。
- **動詞リソース**: Vertex AI は AIP-136 の `:predict` custom verb を採用。他社は flat な `/recommendations` `/rank` 等。
- **seed 種別の表現**: AWS Personalize / Algolia は body discriminator、Recombee は path セグメント、Vertex は servingConfig ID、Spotify は query string。
- **バルク**: 同期バルクは Algolia 流 `{"requests": [...]}` が代表的。Vertex は同期バルクなし。
- **部分失敗**: 同期バルクをサポートする API は HTTP 200 + 要素ごとの status / error code を返す方式が標準。

本 v1 設計は **Vertex の custom verb 命名 + Algolia の batch body + recotem 固有の「recipe を一級リソースに置く」** のハイブリッドを採る。

## 4. API Specification

### 4.1 Endpoint Catalogue

| # | Method | Path | Purpose |
|---|---|---|---|
| 1 | POST | `/v1/recipes/{name}:recommend` | user → items (single) |
| 2 | POST | `/v1/recipes/{name}:recommend-related` | seed items → items (single) |
| 3 | POST | `/v1/recipes/{name}:batch-recommend` | user バルク |
| 4 | POST | `/v1/recipes/{name}:batch-recommend-related` | related バルク |
| 5 | GET  | `/v1/recipes` | recipe 一覧 |
| 6 | GET  | `/v1/recipes/{name}` | recipe メタ (capability 通告) |
| 7 | GET  | `/v1/health` | unauthenticated liveness |
| 8 | GET  | `/v1/health/details` | authenticated diagnostics |
| 9 | GET  | `/v1/metrics` | Prometheus exposition (`include_in_schema=False`) |

旧 `POST /predict/{name}` / `GET /health` / `GET /health/details` / `GET /models` / `GET /metrics` は **削除** (詳細 §6)。

### 4.2 Path Conventions
- `{name}` は recipe 名。正規表現 `^[A-Za-z0-9_-]{1,64}$` (既存と同一)。
- 動詞はコロン区切りの **custom verb** (`:recommend`, `:recommend-related`, `:batch-recommend`, `:batch-recommend-related`) を用いる。Vertex AI と同形式 (AIP-136)。
- `recipes/{name}` 配下に operation を生やす設計のため、将来 `:rerank` `:similar-users` `:popular` 等を追加する余地が常にある。

### 4.3 Request / Response Schemas (Pydantic v2 想定)

#### 4.3.1 Single Recommend (`:recommend`)

```python
class RecommendRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=256)
    limit: int = Field(default=10, ge=1, le=1000)
    exclude_items: list[str] | None = Field(default=None, max_length=1000)
    # 将来拡張用 (任意)。サーバ側は recipe 個別のロジックで解釈。
    context: dict[str, Any] | None = None
```

```python
class RecommendItem(BaseModel):
    item_id: str
    score: float
    model_config = ConfigDict(extra="allow")  # 既存と同様、metadata join を許容

class RecommendResponse(BaseModel):
    request_id: str
    recipe: str
    model_version: str  # 例 "sha256:abc..." — artifact ヘッダ由来の決定的識別子
    items: list[RecommendItem]
```

#### 4.3.2 Single Related (`:recommend-related`)

```python
class RecommendRelatedRequest(BaseModel):
    seed_items: list[str] = Field(min_length=1, max_length=100)
    limit: int = Field(default=10, ge=1, le=1000)
    exclude_items: list[str] | None = Field(default=None, max_length=1000)
    context: dict[str, Any] | None = None
```

Response は `RecommendResponse` を再利用。

#### 4.3.3 Batch Recommend (`:batch-recommend`)

```python
class BatchRecommendRequest(BaseModel):
    requests: list[RecommendRequest] = Field(min_length=1, max_length=256)

class BatchResultEntry(BaseModel):
    index: int
    status: Literal["ok", "error"]
    items: list[RecommendItem] | None = None
    error: ErrorDetail | None = None

class ErrorDetail(BaseModel):
    code: str  # 既存構造化エラーと共通の enum (UNKNOWN_USER, EMPTY_SEED, ...)
    message: str

class BatchRecommendResponse(BaseModel):
    request_id: str
    recipe: str
    model_version: str
    results: list[BatchResultEntry]
```

#### 4.3.4 Batch Related (`:batch-recommend-related`)
構造は §4.3.3 と対称。`requests` の各要素は `RecommendRelatedRequest`。

#### 4.3.5 Recipe Discovery (`GET /v1/recipes`, `GET /v1/recipes/{name}`)

```python
class RecipeSummary(BaseModel):
    name: str
    model_version: str
    loaded_at: datetime          # hot-swap で最後にロードした時刻
    supported_verbs: list[str]   # 例 ["recommend", "recommend-related", "batch-recommend", "batch-recommend-related"]
    kind: str                    # "user-item" | "item-item" | (将来) "popularity" など

class RecipesListResponse(BaseModel):
    recipes: list[RecipeSummary]

class RecipeDetailResponse(RecipeSummary):
    config_digest: str           # recipe.yaml の sha256 (artifact ヘッダから引く)
    algorithms: list[str]        # 学習時に試行されたアルゴリズム
    best_algorithm: str
```

### 4.4 Status Codes & Error Codes

| HTTP | code | When |
|---|---|---|
| 200 | – | success (バルクの部分失敗は要素単位の `status:"error"`) |
| 400 | INVALID_REQUEST | スキーマ違反 (422 とは別レイヤ) |
| 401 | UNAUTHENTICATED | `X-API-Key` 欠落 |
| 403 | FORBIDDEN | キー無効 |
| 404 | RECIPE_NOT_FOUND | `{name}` がレジストリに無い |
| 404 | UNKNOWN_USER | (single recommend のみ) `user_id` が idmap に無い |
| 404 | UNKNOWN_SEED_ITEMS | (single related のみ) `seed_items` が全て idmap に無い |
| 422 | VALIDATION_ERROR | Pydantic バリデーション失敗 |
| 503 | RECIPE_UNAVAILABLE | recipe がロード中、もしくは stale だが degraded で配信不能 |
| 500 | INTERNAL_ERROR | 想定外例外 |

部分失敗 (バルク): 全体 HTTP 200 + `results[].status="error"` + `results[].error.code` で表現。**全リクエスト失敗ケース** (例: recipe 未ロード) は HTTP 503 を返し `results` は返さない。

### 4.5 Authentication

既存通り `X-API-Key`。`/v1/health` のみ unauth、それ以外は全て認証必須 (`/v1/metrics` 含む)。

### 4.6 Headers

- リクエスト: `X-Request-ID` を受け取り、無ければサーバが付与。response にも echo。
- レスポンス: `X-Recotem-Metadata-Degraded`, `X-Recotem-Model-Version` (新規, model_version の重複露出で監視容易)。

### 4.7 Metrics

`request_recipe_status{recipe, verb, status}` を中心ラベル構成とする。
- `verb` ∈ `recommend | recommend_related | batch_recommend | batch_recommend_related`
- `status` ∈ `ok | unknown_user | unknown_seed_items | unavailable | error`
- バルクのレイテンシは要素数を `verb` ラベルとは別の `batch_size` ヒストグラムで分離計測。

## 5. Implementation Outline

### 5.1 File-level Changes (`src/recotem/serving/`)

- `routes.py` — 全面書き換え。v1 ルーターを `make_v1_router()` factory で構築し、9 エンドポイントを実装。既存の `make_router` は削除。
- `schemas.py` (新規もしくは既存拡張) — §4.3 のモデルを定義。
- `app.py` — `app.include_router(make_v1_router(), prefix="/v1")` に変更。CORS、exception handler、dev mode `/docs` は v1 配下に移植。
- `registry.py` — loaded recipe entry に `supported_verbs: list[str]` と `kind: str` を保持。`kind` は artifact ヘッダのアルゴリズムから決定 (例: TopPop/IALS/BPR/RP3beta はいずれも `user-item` モデルなので default `user-item`; 将来 item-only モデルが入ったら `item-item` を返す)。
- `metrics.py` — ラベル名拡張。`verb`, `batch_size` ヒストグラム追加。

### 5.2 Internal Plumbing

- user → items: 既存 `entry.recommender.get_recommendation(user_id, cutoff, ...)` を継続利用。
- items → items: 既存 `IDMappedRecommender.get_recommendation_for_new_user(seed_items, cutoff)` を呼ぶ (`src/recotem/_idmap.py:114-124`)。
- バルク (user): irspack の `recommend_batch` (もしくは複数 user の `get_recommendation` を内部で並列化) を活用。実装の最初のイテレーションでは for-loop で逐次処理し、profile を見てから並列化判断。
- バルク (related): `recommend_for_new_user_batch` を活用 (irspack 側に存在することは調査済み)。

### 5.3 Tests

- `tests/serving/test_v1_recommend.py` — single recommend success / unknown user / validation
- `tests/serving/test_v1_recommend_related.py` — single related success / unknown seed / empty seed
- `tests/serving/test_v1_batch_recommend.py` — 部分失敗、上限超過、全失敗時の 503
- `tests/serving/test_v1_batch_recommend_related.py` — 同上
- `tests/serving/test_v1_recipes.py` — discovery エンドポイント
- 既存の `tests/serving/test_predict.py` 等は削除 (旧APIの撤去に伴う)。

### 5.4 Documentation Updates

- `README.md` — Quickstart の curl 例を `:recommend` 形式に置換、`:recommend-related` 例も追記。
- `docs/getting-started.md` — endpoint 一覧を v1 ベースに刷新。
- `docs/operations.md` — SLO 表に `verb` ラベル反映。
- `docs/security.md` — 認証境界の説明を v1 path に合わせ更新。
- `docs/recipe-reference.md` — **変更なし**。
- 新規 `docs/api-reference.md` — v1 全エンドポイントの正典としてのリファレンス。
- 新規 `docs/migration-v1.md` — alpha 旧API → v1 のマッピング表 (`POST /predict/{name}` → `POST /v1/recipes/{name}:recommend` 等)。

## 6. Migration

alpha 段階のため互換シムは設けず、旧パスは **削除**。`CHANGELOG.md` (もしくは README の Changelog 節) に「v1.0 で alpha API を全廃」と明記し、`docs/migration-v1.md` に旧→新パスのマッピング表を置く。

| 旧 (alpha) | 新 (v1) |
|---|---|
| `POST /predict/{name}` | `POST /v1/recipes/{name}:recommend` |
| `GET /health` | `GET /v1/health` |
| `GET /health/details` | `GET /v1/health/details` |
| `GET /models` | `GET /v1/recipes` |
| `GET /metrics` | `GET /v1/metrics` |

## 7. Future Extension Points

- `POST /v1/recipes/{name}:rerank` — `{user_id, candidate_items[]}` を受けて並べ替え (AWS Personalize の `GetPersonalizedRanking` 相当)。
- `POST /v1/recipes/{name}:similar-users` — seed user → users。
- `POST /v1/recipes/{name}:popular` — seed なし、人気上位を返す (非個人化モデル収容)。
- `kind` 別の capability negotiation を `GET /v1/recipes/{name}` の `supported_verbs` で公開しているため、クライアントは事前検出可能。

## 8. Risks & Open Questions

- **FastAPI/Starlette が `:` 含む path を扱えるか**: AIP-136 形式 (`/v1/recipes/{name}:recommend`) は Starlette のパスマッチングと OpenAPI 公開で問題ないか実装初期に POC で検証する。FastAPI の path コンバーターは `:` を予約していないため通る見込みだが、`response.openapi_schema` / Swagger UI の動作・URL クライアントの自動エスケープ挙動を含めて確認する。**もし不具合があれば fallback として `/v1/recipes/{name}/recommend` のスラッシュ区切りに切り替える**。本決定は POC 結果次第。
- **`name` パラメタが `:` を含まないこと**: recipe 名の正規表現 `^[A-Za-z0-9_-]{1,64}$` は `:` を除外しているため、 path の最終 `:` 以降を verb として分解可能。これは明示的に regex で担保されている。
- **idmap の seed 部分一致**: `seed_items` の一部だけが既知の場合、既知分だけで推論するか全リジェクトするか。**現案: 既知分のみで推論し、`response.items[]` が空になれば 404 UNKNOWN_SEED_ITEMS、空でなければ 200**。要レビュー。
- **バルクの並列性**: 初版は逐次でも 256 件 × ~10ms = ~2.5s 程度。SLO によっては並列化必要。初版逐次で profile してから判断。
- **`context` フィールド**: 現時点では 未使用フィールド。将来の cold-start / feature-based モデルのために予約。recipe 側で消費されなければ無視 (フォワード互換)。
- **`model_version` の決定的識別子**: 現状 artifact 全体の HMAC 署名 SHA-256 を流用する想定。recipe digest と区別が必要なら別フィールドに切る。

## 9. Acceptance Criteria

- [ ] 全 9 エンドポイントのユニットテストが green。
- [ ] `make test` + ruff/mypy が無警告で通る。
- [ ] `recotem serve --recipes examples/quickstart/` でローカル起動し、README の curl が新形式で動く。
- [ ] `docs/migration-v1.md` が公開可能な品質で存在。
- [ ] OpenAPI スキーマ (`/v1/openapi.json`) が 9 エンドポイントを正しく公開。
