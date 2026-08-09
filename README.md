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
- `AWS_S3_CUSTOM_DOMAIN`（公開用カスタムドメインを使う場合）
- `AWS_QUERYSTRING_AUTH=False`（公開画像の場合）

R2側では公開配信方法・CORS・カスタムドメインを別途設定してください。資格情報やbucket名をGitへcommitしないでください。商品画像、作品画像、出版物、Taiwan Pay QRはすべて同じstorage abstractionを使います。アップロード時は容量、MIME、Pillowによる画像整合性を検証します。

## Email

SMTP互換設定です。特定配信会社に依存しません。

- `EMAIL_HOST`
- `EMAIL_PORT`
- `EMAIL_HOST_USER`
- `EMAIL_HOST_PASSWORD`
- `EMAIL_USE_TLS`
- `DEFAULT_FROM_EMAIL`
- `ORDER_NOTIFICATION_EMAIL`

`EMAIL_HOST`未設定の開発環境はconsole backendです。問い合わせ／注文は先にDBへ保存し、commit後にメールを送るため、メール障害で受付データは失われません。

## LINE Login / Messaging API

購入ではLINE LoginとRfull公式アカウントの友だち追加が必須です。ブラウザ上の自己申告チェックではなく、callbackでLINE Login APIのFriendship Status APIを呼び、`friendFlag=true`を確認したsessionだけが注文を送信できます。LINE access token、authorization code、PKCE verifierはDBへ保存せず、callback処理後に破棄します。

必要な環境変数：

- `LINE_LOGIN_CHANNEL_ID`
- `LINE_LOGIN_CHANNEL_SECRET`
- `LINE_LOGIN_CALLBACK_URL`（本番: `https://restfull.com/auth/line/callback/`）
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

WebhookはJSON parse前のraw bodyとMessaging API Channel Secretを使い、`x-line-signature`をHMAC-SHA256で検証します。署名不正・未添付のpayloadは処理しません。`webhookEventId`を保存して再送を冪等処理し、follow/unfollowを`LineCustomer`の友だち・ブロック状態へ反映します。Checkoutの正本確認はWebhookだけに依存せず、LINE Login APIの結果を必須にしています。

参考: [LINE Login web integration](https://developers.line.biz/en/docs/line-login/integrate-line-login/)、[Add Friend Option](https://developers.line.biz/en/docs/line-login/link-a-bot/)、[Webhook signature](https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/)

## LINE Developers Console設定

1. Rfull用LINE Providerを作成、または既存Providerを確認します。
2. Rfull公式アカウントのMessaging API ChannelをそのProvider配下に作成します。
3. LINE Login Channelを必ず同じProvider配下に作成します。同じユーザーについてLoginとMessagingで同じLINE user IDを得るための前提です。
4. LINE Login ChannelのBasic settingsで、同ProviderのRfull公式LINEアカウントを`Linked LINE Official Account`としてリンクします。友だち追加をプログラムから強制する設定ではありません。
5. LINE Login ChannelのWeb app callbackへ本番`https://restfull.com/auth/line/callback/`を登録します。開発用Channelまたは許可されたcallbackとして`http://localhost:8000/auth/line/callback/`も環境ごとに登録します。
6. Messaging API ChannelのWebhook URLへ`https://restfull.com/webhooks/line/messaging/`を登録します。
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

## Admin運用

### 商品

`商品 > 商品`で名前、SKU、価格（Decimal）、在庫、預購、説明、公開状態、表示順を編集します。商品編集画面のinlineから画像と自由仕様（材質、サイズ、産地等）を追加・削除・差し替え・並び替えできます。正式画像未登録時は旧デモと同じCSSプレースホルダーを表示します。

### 注文処理

1. 顧客がLINE Loginと友だち状態確認を完了して注文すると、LINEへ注文受付通知を送ります。注文受付時、送料と最終金額は「未確定」です。
2. 通常商品だけ在庫が確保されます。預購商品は通常在庫を減らしません。
3. 商品と配送方法を確認し、注文編集画面で`shipping_fee`を入力して保存します。
4. `final_total = 注文時の商品合計スナップショット + 送料`がサーバー側で計算されます。
5. 詳細画面の`確定運費並發送付款通知`を押します。入力だけでは通知されません。操作時に送料、キャンセル/入金状態、LINE購入者、友だち状態を検証し、安全な支払いリンク付きFlex Messageを送ります。
6. Taiwan Pay / 銀行振込は`支払い`inlineへ方法を登録後、`確認入金`を押します。金額は最終金額と一致する必要があり、PaymentがconfirmedになるとOrderをpaidへ変更して入金確認通知を一度だけ送ります。
7. オンラインProviderの成功画面は入金の正本ではありません。Provider実装時は署名検証済みWebhookだけからconfirmedへ変更します。抽象interfaceは`orders/payment_providers.py`にあり、架空Providerは登録していません。
8. 配送会社、追跡番号、任意の追跡URLを保存後、`標記為已出貨並發送 LINE 通知`を押します。confirmed Paymentがない注文は発送できません。
9. 明示的な再送ボタンと失敗通知の安全なretry actionがあります。通常の保存・reloadでは再送しません。
10. 入金確認後、原則3営業日以内を目安に発送します。

注文をキャンセルすると、注文時に確保した通常在庫だけを一度だけ復元します。OrderItemは商品名・SKU・単価・数量・小計のスナップショットで、商品削除後も履歴を保持します。

### 支払方法

`支払方法設定`に台灣 Pay、クレジットカード、銀行振込、PayPalの初期レコードがありますが、すべて初期状態は無効です。実際に準備できた方法だけを有効化してください。

- Taiwan Pay: QR画像をSite Settingsまたは支払方法設定へ登録
- 銀行振込: Site Settingsへ銀行情報を登録し、案内本文を確定
- クレジットカード / PayPal: 正式provider契約・資格情報・hosted checkout実装完了まで有効化しない

カード番号をDjango DBへ保存する実装はありません。Provider固有参照はPayment側へ保持し、Orderへ直書きしていません。

### 問い合わせ

問い合わせはDB保存後、選択分類の担当者1名へ通知し、入力者へ確認メールを送ります。初期ルーティング：

- 設計諮詢 / 媒體合作 / 商業合作: `vicky725705@gmail.com`
- 購物 / 訂單: `rfullshop@gmail.com`

Adminで新規・対応中・完了・スパムを絞り込み、対応メモを残せます。

### Site Settings

ブランド名、公開漢字表記、電話、各用途Email、Facebook / Instagram / LINE URL、LINE客服時間、銀行情報、Taiwan Pay QR、注文通知Email、checkout有効化を編集できます。SNS/LINE URLが空ならリンク自体を表示しません。

### 室内設計・出版物・ポリシー

室内設計は複数画像inline、場所、年、面積、スタイル、説明、表示順を管理します。出版物は号数、タイトル、日付、表紙、featured、公開状態を管理します。

以下は初期状態で本文空・下書きです。法務確認後に本文を入力して公開してください。

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

- canonical: `https://restfull.com`
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

このリポジトリから実際のRender作成・DNS変更は行っていません。

## Domain / DNS

canonicalは`https://restfull.com`です。Renderでcustom domain `restfull.com`を追加し、Renderが提示するDNSレコードをドメイン管理画面へ設定してください。`www.restfull.com`はapexへ301転送する方針です。アプリ側にもwww→apexの本番301 middlewareがあります。

HTTPS証明書が有効になり、`/healthz/`とAdminを確認してからDNSを切り替えてください。

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
- [ ] Callback `https://restfull.com/auth/line/callback/`を登録
- [ ] Webhook `https://restfull.com/webhooks/line/messaging/`を登録、有効化、Verify
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
