# Rfull / RESTFULL ATELIER EC・CMS

靜院居家のAstro静的デモを、既存の余白・明朝体・画像比率・レスポンシブ・フェード／ホバー表現を維持しながら、Django 5.2 LTS + PostgreSQLで本番運用できるEC / CMSへ移行したプロジェクトです。

旧Astroソース（`src/`、`public/`、`astro.config.mjs`）はデザイン参照用として残しています。本番アプリはDjango Templatesを使用し、別SPAやREST APIフロントはありません。

## 技術構成

- Python 3.12 / Django 5.2 LTS
- PostgreSQL（ローカルでは未指定時のみSQLiteへフォールバック）
- Django Templates / Django Admin
- Gunicorn / WhiteNoise
- Cloudflare R2等のS3互換ストレージ（`django-storages` + `boto3`）
- LINE Login v2.1（OIDC / state / nonce / PKCE S256 / Add Friend Option）
- LINE Messaging API（Flex Message / Webhook署名検証 / retry key）
- Render Blueprint / GitHub Actions

## Django apps

- `core`: Site Settings、共通設定、トップ／About、robots、health check
- `catalog`: 商品、カテゴリ、複数画像、自由仕様、検索
- `orders`: session cart、checkout、注文、注文明細、送料、支払い
- `inquiries`: 問い合わせ分類、フォーム、スパム対策、Email
- `content`: 室内設計、出版物、ポリシーページ

## ローカル起動

Python 3.10以上（推奨3.12）とPostgreSQLを用意します。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Django自身は`.env`を自動読込しないため、シェルやdirenv等で環境変数を読み込んでください。例：

```bash
set -a
source .env
set +a
python manage.py migrate
python manage.py seed_initial_data
python manage.py createsuperuser
python manage.py runserver
```

`seed_initial_data`は旧静的デモの商品13件、カテゴリ6件、室内設計4件、出版物4件、問い合わせ分類、支払方法4件、ポリシー下書きを投入します。既存レコードは上書きしないため、Admin運用開始後に再実行しても顧客編集内容を破壊しません。

## PostgreSQL

例：

```bash
createdb restfull
export DATABASE_URL=postgresql://localhost/restfull
python manage.py migrate
```

`DATABASE_URL`未指定時だけ開発用SQLiteを使用します。本番は必ずPostgreSQL URLを設定してください。

## Static / Media

既存の`src/styles/global.css`をDjango staticとして直接再利用し、新機能分は`static/css/app.css`、共通操作は`static/js/site.js`へ追加しています。

```bash
python manage.py collectstatic --noinput
```

開発環境のアップロードは`media/`へ保存されます。本番でRenderのローカルファイルシステムへアップロードを保存してはいけません。

## Cloudflare R2 / S3互換ストレージ

次を設定するとDjangoのdefault storageがS3互換へ切り替わります。

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_STORAGE_BUCKET_NAME`
- `AWS_S3_ENDPOINT_URL`（R2 API endpoint）
- `AWS_S3_REGION_NAME=auto`
- `AWS_S3_CUSTOM_DOMAIN`（公開用のR2カスタムドメインまたは`r2.dev`ドメインを使う場合）
- `AWS_QUERYSTRING_AUTH=True`（公開ドメインを使わず、R2 API endpointから署名付きURLで配信する場合）
- `AWS_QUERYSTRING_EXPIRE=3600`（署名付きURLの有効期間／秒）

R2への保存とブラウザへの公開配信は別の設定です。公開ドメインを設定しない場合は`AWS_QUERYSTRING_AUTH=True`のまま運用してください。公開カスタムドメインに切り替える場合のみ`AWS_S3_CUSTOM_DOMAIN`を設定し、公開バケットで`AWS_QUERYSTRING_AUTH=False`を使えます。R2側では必要に応じて公開配信・CORS・カスタムドメインを設定してください。資格情報やbucket名をGitへcommitしないでください。商品画像、作品画像、出版物、Taiwan Pay QRはすべて同じstorage abstractionを使います。アップロード時は容量、MIME、Pillowによる画像整合性を検証します。

## Email

SMTP互換設定です。特定配信会社に依存しません。

- `EMAIL_HOST`
- `EMAIL_PORT`
- `EMAIL_HOST_USER`
- `EMAIL_HOST_PASSWORD`
- `EMAIL_USE_TLS`
- `DEFAULT_FROM_EMAIL`
- `ORDER_NOTIFICATION_EMAIL`
- `ORDER_NOTIFICATION_EMAILS`（カンマ区切りの追加通知先）

`EMAIL_HOST`未設定の開発環境はconsole backendです。問い合わせ／注文は先にDBへ保存し、commit後にメールを送るため、メール障害で受付データは失われません。

## LINE Login / Messaging API

購入ではLINE LoginとRfull公式アカウントの友だち追加が必須です。ブラウザ上の自己申告チェックではなく、Friendship Status API、LINE Login callbackの`friendship_status_changed=true`、または署名検証済みfollow Webhookで友だち状態を確認したsessionだけが注文を送信できます。LINE access token、authorization code、PKCE verifierはDBへ保存せず、callback処理後に破棄します。

必要な環境変数：

- `LINE_LOGIN_CHANNEL_ID`
- `LINE_LOGIN_CHANNEL_SECRET`
- `LINE_LOGIN_CALLBACK_URL`（本番: `https://restfull-xhex.onrender.com/auth/line/callback/`）
- `LINE_MESSAGING_CHANNEL_ACCESS_TOKEN`
- `LINE_MESSAGING_CHANNEL_SECRET`
- `LINE_OFFICIAL_ACCOUNT_BASIC_ID`（例: `@...`。実値はDashboardのみ）
- `LINE_API_TIMEOUT`（既定5秒）
- `LINE_FRIENDSHIP_MAX_AGE`（Friendship API確認をcheckout sessionで認める秒数、既定900秒）
- `PAYMENT_LINK_MAX_AGE`（署名付き支払いリンク有効秒数、既定604800秒）

公開endpoint：

- Login開始: `/auth/line/login/`
- Login callback: `/auth/line/callback/`
- Messaging webhook: `/webhooks/line/messaging/`
- 署名付き支払いページ: `/pay/<signed-token>/`

Login authorization requestは`response_type=code`、`scope=openid profile`、`bot_prompt=aggressive`、`state`、`nonce`、`code_challenge_method=S256`を使用します。email scopeは要求しません。ID tokenはLINEのverify endpointで検証後、issuer、audience、expiration、nonce、subjectもサーバー側で確認します。

Messaging APIのChannel Access Tokenはサーバー側だけで使用します。通知はOrder commit後に送信し、失敗しても注文・送料・入金・発送DB更新はrollbackしません。各送信を`LineNotification`へ保存し、通知種別とdedupe keyのunique制約、LINEの`X-Line-Retry-Key`で二重送信を防ぎます。失敗通知はAdminから同じretry keyで再試行できます。

WebhookはJSON parse前のraw bodyとMessaging API Channel Secretを使い、`x-line-signature`をHMAC-SHA256で検証します。署名不正・未添付のpayloadは処理しません。`webhookEventId`を保存して再送を冪等処理し、follow/unfollowを`LineCustomer`の友だち・ブロック状態へ反映します。友だち追加画面からCheckoutへ戻った際は、ログイン開始後のfollow Webhookをポーリングで検出して配送フォームを開放します。

参考: [LINE Login web integration](https://developers.line.biz/en/docs/line-login/integrate-line-login/)、[Add Friend Option](https://developers.line.biz/en/docs/line-login/link-a-bot/)、[Webhook signature](https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/)

## LINE Developers Console設定

1. Rfull用LINE Providerを作成、または既存Providerを確認します。
2. Rfull公式アカウントのMessaging API ChannelをそのProvider配下に作成します。
3. LINE Login Channelを必ず同じProvider配下に作成します。同じユーザーについてLoginとMessagingで同じLINE user IDを得るための前提です。
4. LINE Login ChannelのBasic settingsで、同ProviderのRfull公式LINEアカウントを`Linked LINE Official Account`としてリンクします。友だち追加をプログラムから強制する設定ではありません。
5. LINE Login ChannelのWeb app callbackへ本番`https://restfull-xhex.onrender.com/auth/line/callback/`を登録します。開発用Channelまたは許可されたcallbackとして`http://localhost:8000/auth/line/callback/`も環境ごとに登録します。
6. Messaging API ChannelのWebhook URLへ`https://restfull-xhex.onrender.com/webhooks/line/messaging/`を登録します。
7. `Use webhook`を有効にし、Consoleの`Verify`を実行します（空eventsにもHTTP 200を返します）。
8. LINE Login Channel ID / Channel SecretをRenderへ設定します。
9. Messaging API Channel Secret / Channel Access TokenをRenderへ設定します。
10. 公式アカウントのBasic IDを`LINE_OFFICIAL_ACCOUNT_BASIC_ID`へ設定します。
11. LINE Login Channelに公式アカウントが正しくリンクされ、Login時に`bot_prompt=aggressive`の友だち追加画面が出ることを確認します。
12. AdminのSite Settingsへ正式な友だち追加URLを入力します。
13. 本番スマートフォンでLogin、友だち追加、Friendship確認、テスト注文、4種類の通知を通し確認します。

同意なしに友だち追加することはできません。ユーザーが追加を拒否した場合、Cartは保持しますが注文は作成しません。

## Turnstile・rate limit

- `TURNSTILE_SITE_KEY`
- `TURNSTILE_SECRET_KEY`
- `CONTACT_RATE_LIMIT`（既定10分あたり5回）
- `CHECKOUT_RATE_LIMIT`（既定10分あたり5回）

キー未設定時はTurnstile検証をスキップするため開発は停止しません。本番は両キーを設定してください。問い合わせ／注文フォームにはサーバー側validation、honeypot、rate limitがあり、全POSTはCSRF保護対象です。

## 自訂管理後台操作說明

`/admin/` 是「靜院居家 營運管理」後台，延用 Django 原有的資料、權限與操作邏輯。營運儀表板集中顯示待處理訂單、新諮詢、近 30 日付款、庫存提醒與 LINE 通知失敗。每位管理員可看到的數據與功能，均依 Django 模型權限控制。

訂單詳情會依目前狀態顯示下一步操作。傳送通知或取消訂單前會顯示確認視窗；庫存、金額與 LINE 通知仍使用原有的伺服器端處理邏輯。

### 商品

在「商品管理 > 商品」編輯名稱、SKU、售價、庫存、預購說明、公開狀態與顯示順序。商品編輯頁可新增、刪除、替換或調整圖片與商品規格（材質、尺寸、產地等）的順序。尚未上傳正式圖片時，會顯示預留圖片。

### 訂單處理

1. 顧客完成 LINE Login 與好友狀態確認後送出訂單，系統會傳送訂單成立通知。此時運費與訂單總額仍為「尚未確定」。
2. 一般商品會保留庫存；預購商品不會扣除一般庫存。
3. 確認商品與配送方式後，在訂單編輯頁填寫 `shipping_fee` 並儲存。
4. 伺服器會計算 `final_total = 訂單成立時的商品小計快照 + 運費`。
5. 點選「確定運費並寄送付款通知」。系統會檢查運費、訂單狀態、LINE 購買者與好友狀態，再傳送含安全付款連結的 Flex Message。
6. Taiwan Pay 或銀行轉帳需先新增付款記錄，再點選「確認收款」。金額必須與訂單總額相同。確認後訂單會更新為已付款，並僅傳送一次付款確認通知。
7. 線上金流的成功頁面不是付款懑據。導入金流服務時，只能由已驗證簽章的 Webhook 將狀態更新為 confirmed。
8. 儲存物流公司、追蹤號碼與追蹤網址後，點選「標記已出貨並傳送通知」。尚未確認付款的訂單不能出貨。
9. 後台提供明確的重新傳送按鈕與安全重試功能。一般儲存或重新載入頁面不會重複傳送。
10. 確認付款後，原則上於 3 個工作日內安排出貨。

取消訂單時，系統只會將訂單成立時保留的一般庫存還原一次。OrderItem 會保留商品名稱、SKU、單價、數量與小計快照，即使商品被刪除仍能保留歷史資料。

### 付款方式

「付款方式設定」預設包含台灣 Pay、信用卡、銀行轉帳與 PayPal，初始狀態全部為停用。只啟用已完成正式設定的付款方式。

- Taiwan Pay：在網站設定或付款方式中上傳 QR 圖片
- 銀行轉帳：在網站設定中填寫銀行資訊，並確認付款說明
- 信用卡 / PayPal：完成正式契約、憑證與 hosted checkout 導入前不得啟用

系統不會將信用卡號碼儲存到 Django 資料庫。金流服務商的參考編號僅保存於 Payment，不會直接寫入 Order。

### 客戶諮詢

諮詢資料儲存後，系統會通知所選分類的擔當人，並寄送確認信給留言者。預設寄送規則：

- 設計諮詢 / 媒體合作 / 商業合作: `vicky725705@gmail.com`
- 購物 / 訂單: `rfullshop@gmail.com`

Admin 可依新留言、處理中、已完成或垃圾訊息篩選，並留下管理備註。

### 網站設定

可編輯品牌名稱、公開顯示名稱、電話、各用途 Email、Facebook / Instagram / LINE 網址、LINE 客服時間、銀行資訊、Taiwan Pay QR、訂單通知 Email 與結帳功能。SNS 或 LINE 網址為空時，前台不會顯示對應連結。

### 室內設計、出版刊物與政策頁面

室內設計可管理多張圖片、地點、年份、面積、風格、說明與顯示順序。出版刊物可管理期號、標題、出版日期、封面、精選與公開狀態。

以下政策頁面初始為空白草稿。請完成法務確認後填寫內文並公開。

- 隱私權政策
- 購物須知
- 付款方式
- 配送政策
- 預購商品說明
- 退換貨政策

## 注文導線の安全策

本番・開発とも`checkout_enabled=True`だけでは不十分です。6個のLINE必須環境変数とSite Settingsの友だち追加URLが揃わない限りCheckoutは注文を受け付けません。ローカルでは開発用LINE Channelの値を使用してください。

公開前チェック：

```bash
python manage.py check_launch_readiness
```

DEBUG、HTTPS canonical/callback、6個のLINE設定、Site SettingsのLINE URL、Payment Method、注文Email、S3/R2 storage、必須ポリシー本文・公開状態、SEO、checkoutを一覧確認します。失敗項目があれば非0終了します。

## SEO

- canonical: `https://restfull-xhex.onrender.com`
- `SEO_INDEX_ENABLED=False`: 全ページnoindex、robotsで全拒否（開発・staging既定）
- `SEO_INDEX_ENABLED=True`: 公開コンテンツをindex可能にし、cart / checkout / 完了 / 支払い案内はnoindex
- 動的`robots.txt`、`sitemap.xml`
- title、description、OGP、favicon
- Organization、Product structured data
- 商品、カテゴリ、作品、出版物、公開ポリシーをsitemapへ追加

本番コンテンツとドメイン接続が終わるまで`SEO_INDEX_ENABLED=True`にしないでください。

## Production security

`DEBUG=False`で次が有効です：secure session/CSRF cookies、HTTPS redirect、proxy SSL header、HSTS、X-Content-Type-Options、strict referrer policy、frame deny。SECRET_KEY未設定では起動しません。

Admin URL変更だけには依存していません。強いsuperuser password、最小限のstaff権限、定期的な権限見直し、Render/R2/Email資格情報のローテーションを行ってください。

## Render deployment

`render.yaml`はSingapore regionのWeb ServiceとPostgreSQLを定義します。

- build: dependency install + `collectstatic`
- pre-deploy: `python manage.py migrate`
- initial deploy hook: `python manage.py seed_initial_data`
- start: Gunicorn
- health check: `/healthz/`

migrationをGunicorn起動コマンドへ混ぜていないため、複数workerから同時実行されません。

1. GitHub repositoryをRender Blueprintへ接続します。
2. `sync: false`のEmail / R2 / Turnstile / LINE Channel ID・Secret・Access Token・Basic IDをDashboardで入力します。
3. 初回deploy後、Render Shellで`python manage.py createsuperuser`を実行します。
4. AdminでSite Settings、画像、ポリシー、支払方法を設定します。
5. `python manage.py check_launch_readiness`が通るまでcheckout/SEOを有効化しません。

### Render environment variables

Blueprintが自動設定する値：

- `DJANGO_SECRET_KEY`: Renderの`generateValue`で生成。自分で値を作る必要はありません。
- `DATABASE_URL`: Blueprint内の`restfull-db` PostgreSQLのconnection stringを`fromDatabase`で接続。
- `RENDER_EXTERNAL_HOSTNAME` / `RENDER_EXTERNAL_URL` / `PORT`: Renderが自動提供するため手動登録しません。

`render.yaml`で固定済みの非秘密値：

- `PYTHON_VERSION=3.12.13`
- `DJANGO_DEBUG=False`
- `ALLOWED_HOSTS=restfull-xhex.onrender.com`
- `CSRF_TRUSTED_ORIGINS=https://restfull-xhex.onrender.com`
- `CANONICAL_ORIGIN=https://restfull-xhex.onrender.com`
- `CANONICAL_REDIRECT_HOSTS=`
- `SEO_INDEX_ENABLED=False`（公開準備完了後のみ`True`）
- `DJANGO_SECURE_SSL_REDIRECT=True`
- `DJANGO_HSTS_SECONDS=31536000`
- `EMAIL_PORT=587` / `EMAIL_USE_TLS=True`
- `ORDER_NOTIFICATION_EMAIL=rfullshop@gmail.com`
- `ORDER_NOTIFICATION_EMAILS`（追加通知先をカンマ区切りでRenderの非公開環境変数へ設定）
- `AWS_S3_REGION_NAME=auto` / `AWS_QUERYSTRING_AUTH=True` / `AWS_QUERYSTRING_EXPIRE=3600`
- `MAX_IMAGE_UPLOAD_MB=10`
- `CONTACT_RATE_LIMIT=5` / `CHECKOUT_RATE_LIMIT=5`
- `LINE_LOGIN_CALLBACK_URL=https://restfull-xhex.onrender.com/auth/line/callback/`
- `LINE_API_TIMEOUT=5` / `LINE_FRIENDSHIP_MAX_AGE=900`
- `PAYMENT_LINK_MAX_AGE=604800`

Render Dashboardで実値を入力する`sync: false`項目：

- Email: `DEFAULT_FROM_EMAIL`、`EMAIL_HOST`、`EMAIL_HOST_USER`、`EMAIL_HOST_PASSWORD`、`ORDER_NOTIFICATION_EMAILS`
- R2: `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`、`AWS_STORAGE_BUCKET_NAME`、`AWS_S3_ENDPOINT_URL`、`AWS_S3_CUSTOM_DOMAIN`
- Turnstile: `TURNSTILE_SITE_KEY`、`TURNSTILE_SECRET_KEY`
- LINE: `LINE_LOGIN_CHANNEL_ID`、`LINE_LOGIN_CHANNEL_SECRET`、`LINE_MESSAGING_CHANNEL_ACCESS_TOKEN`、`LINE_MESSAGING_CHANNEL_SECRET`、`LINE_OFFICIAL_ACCOUNT_BASIC_ID`

取得元はEmail provider、Cloudflare R2 / Turnstile、LINE Developers Consoleです。秘密値はGitHub、`render.yaml`、HTML、JavaScriptへ記載しません。

このリポジトリから実際のRender作成・DNS変更は行っていません。

## Public URL / Future custom domain

現在の正式な公開originは`https://restfull-xhex.onrender.com`です。canonical、CSRF trusted origin、LINE callback、Messaging webhook、支払いリンクはこのoriginへ統一しています。

将来custom domainへ切り替える場合は、`CANONICAL_ORIGIN`、`CSRF_TRUSTED_ORIGINS`、`ALLOWED_HOSTS`、`LINE_LOGIN_CALLBACK_URL`を同時に変更し、LINE Developers Consoleのcallback・webhookも更新します。旧hostから転送する場合だけ`CANONICAL_REDIRECT_HOSTS`へ旧hostを設定します。

## Tests / CI

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

GitHub ActionsはPostgreSQL 17でsystem check、migration consistency、migrate、testsを実行します。

## Launch checklist

- [ ] 正式LINE公式URL / 友達追加URLを設定
- [ ] Login / Messaging Channelを同じProvider配下に作成
- [ ] Login ChannelへRfull公式LINEをリンク
- [ ] Callback `https://restfull-xhex.onrender.com/auth/line/callback/`を登録
- [ ] Webhook `https://restfull-xhex.onrender.com/webhooks/line/messaging/`を登録、有効化、Verify
- [ ] Renderへ6個のLINE必須環境変数を設定
- [ ] Facebook / Instagram URL（再申請完了後のみ）
- [ ] 注文通知SMTPの送受信テスト
- [ ] Cloudflare R2 bucket、公開配信、CORS、資格情報
- [ ] 正式商品情報・在庫・預購注記・本番画像
- [ ] Taiwan Pay QRと運用手順
- [ ] 銀行名・コード・口座番号・名義
- [ ] クレジットカードprovider契約（未契約のまま有効化しない）
- [ ] PayPal Business / Checkout設定（未設定のまま有効化しない）
- [ ] 配送対応地域と配送会社の運用決定
- [ ] 返品交換・預購・配送・privacy等の法務本文確定
- [ ] Turnstile本番キー
- [ ] 管理者・staff権限と強いpassword
- [ ] `check_launch_readiness`成功
- [ ] Desktop / tablet / mobileで最終実画像確認
- [ ] checkoutテスト注文→送料入力→入金→発送の通し確認
- [ ] `SEO_INDEX_ENABLED=True`
- [ ] DNS切替とwww redirect確認

## 現時点の意図的な制約

- 一般会員登録、ポイント、クーポンはありません（購入者認証はLINE Loginのみ）。
- 送料自動計算はありません。
- 決済provider接続、カード入力、PayPal Checkoutはありません。
- LINE上の商品選択Bot、チャット内注文、LIFFフルEC、Rich Menu、LINE Pay独自連携はありません。
- 配送会社APIはありません。
- 本番画像・未確定ポリシー・SNS・銀行・provider情報は捏造せず空欄／下書きです。
