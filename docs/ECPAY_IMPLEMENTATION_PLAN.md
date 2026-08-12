# ECPay 全方位金流（AIO）実装計画

調査日: 2026-08-12

## 結論

最初のリリースは ECPay の導轉式「全方位金流 AioCheckOut V5」を使い、まず信用卡一次付清だけを有効にする。カード番号は本サイトへ入力・保存せず、ECPay の hosted payment page へブラウザ POST で遷移させる。

支払い完了の正本はユーザーが戻る画面ではなく、署名検証済みの `ReturnURL` 通知とする。現在の `orders/payment_providers.py` は骨組みだけで、外部ページへ送る POST fields、ECPay form-data callback、特店取引番号の保存が不足しているため、PaymentMethod の `provider=ecpay` は下記実装とテストが完了するまで有効にしない。

公式資料:

- [全方位金流付款（AioCheckOut V5）](https://developers.ecpay.com.tw/2864/)
- [付款結果通知](https://developers.ecpay.com.tw/2878/)
- [CheckMacValue 検査コード](https://developers.ecpay.com.tw/2902/)
- [注文照会 QueryTradeInfo/V5](https://developers.ecpay.com.tw/2890/)
- [信用卡請退款](https://developers.ecpay.com.tw/2885/)
- [公式テスト接続情報](https://developers.ecpay.com.tw/45895/)

## 現行コードとの差分

| 項目 | 現状 | 必要な変更 |
| --- | --- | --- |
| Provider 呼び出し | `get_provider()` の存在確認だけ | 選択確定後に checkout fields を生成 |
| ECPay 遷移 | 未実装 | ECPay endpoint へ hidden fields を自動 POST |
| callback | LINE webhook のみ | ECPay `ReturnURL` 専用 endpoint を追加 |
| 署名 | 未実装 | 全受信項目で CheckMacValue を再計算し constant-time 比較 |
| 取引ID | `provider_reference` のみ | `MerchantTradeNo` と ECPay `TradeNo` を別々に保存 |
| 冪等性 | 汎用フィールドは存在 | 同一通知・再送・同時到着を transaction lock で一度だけ確定 |
| 利用者戻り画面 | 未実装 | 支払い状態の表示だけを行い、入金確定には使わない |
| 照会・復旧 | 未実装 | QueryTradeInfo による遅延照合ジョブを追加 |
| 返金 | DB記録のみ | 初期は管理画面で ECPay 側を手動処理。後続で DoAction を追加 |

## 推奨するファイル構成

- `orders/ecpay.py`: endpoint、payload、CheckMacValue、callback parser、query client
- `orders/payment_providers.py`: `ECPayProvider` 登録と hosted-form 用契約
- `orders/views.py`: checkout 開始、ReturnURL、ユーザー戻り画面
- `orders/urls.py` または `config/urls.py`: 公開 callback URL
- `templates/orders/provider_redirect.html`: ECPay へ自動 POST する hidden form
- `orders/migrations/0007_*.py`: ECPay の特店取引番号保存フィールド
- `orders/tests/test_ecpay.py`: 公式ベクトル・改ざん・再送・金額不一致テスト
- `orders/management/commands/reconcile_ecpay_payments.py`: 未確定取引の照会

## 設定値

秘密値は Render の環境変数だけに置き、Git、HTML、ログ、`provider_metadata` へ保存しない。

```text
ECPAY_ENABLED=False
ECPAY_STAGE=True
ECPAY_MERCHANT_ID=
ECPAY_HASH_KEY=
ECPAY_HASH_IV=
ECPAY_API_TIMEOUT=8
```

`ECPAY_STAGE=True` の送信先は `https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5`、本番は `https://payment.ecpay.com.tw/Cashier/AioCheckOut/V5`。ReturnURL は `https://restfull-xhex.onrender.com/webhooks/ecpay/payment/` とする。ECPay の通知要件に合わせ、正式ドメインの HTTPS / 443 で常時応答できるようにする。

起動時チェックには以下を追加する。

- enabled のとき MerchantID / HashKey / HashIV がすべて存在
- stage と `DEPLOYMENT_ENV` / credential set の組合せが矛盾しない
- `CANONICAL_ORIGIN` が HTTPS
- ECPay provider を有効化した PaymentMethod が少なくとも1件ある

## データ設計

`Payment` に以下を追加する。

```python
provider_merchant_trade_no = models.CharField(max_length=20, null=True, blank=True, unique=True)
```

- `provider_merchant_trade_no`: 本サイトが生成して ECPay の `MerchantTradeNo` へ送る英数字20文字以内の一意値
- `provider_reference`: ECPay が返す `TradeNo`
- `provider_event_id`: ECPay に event ID がないため、正常通知では `ecpay:{MerchantTradeNo}:{TradeNo}:paid` の決定的キーを保存
- `provider_metadata`: RtnCode、PaymentType、SimulatePaid、通知受信時刻など、秘密情報・カード番号を除いた監査情報

MerchantTradeNo は注文番号のハイフンを除去するだけに依存せず、Payment ID とランダム成分またはDB採番から生成し、一度保存したら再利用する。同一 Payment の画面再読込で新しい ECPay 注文番号を作らない。

## Checkout 作成フロー

1. 署名付き支払いURLを検証する。
2. 支払い方法と最終規約同意を検証する。
3. DB transaction 内で `Payment` を作成または lock し、`provider_merchant_trade_no` を一度だけ採番する。
4. 以下の AioCheckOut fields をサーバーで生成する。
5. CheckMacValue を付けた hidden form を返し、ブラウザから ECPay へ POST する。

初期リリースの主要 fields:

```text
MerchantID=<環境変数>
MerchantTradeNo=<英数字20文字以内・一意>
MerchantTradeDate=yyyy/MM/dd HH:mm:ss（Asia/Taipei）
PaymentType=aio
TotalAmount=<final_total のTWD整数>
TradeDesc=RESTFULL ATELIER order
ItemName=<商品名を # 区切り、400文字以内>
ReturnURL=https://restfull-xhex.onrender.com/webhooks/ecpay/payment/
ChoosePayment=Credit
EncryptType=1
NeedExtraPaidInfo=N
ClientBackURL=<支払い状態確認ページ>
```

ECPay は `ChoosePayment=ALL` も提供するが、新しい支払い種別が追加された際の受信処理漏れを避けるため、公式も固定指定を推奨している。まず `Credit` のみで開始し、ApplePay や TWQR は契約状況と個別テスト後に別 PaymentMethod として追加する。現在の静的 QR の Taiwan Pay と ECPay の `TWQR` は別機能として扱う。

`OrderResultURL` は `ReturnURL` より先に到着する場合も後に到着する場合もあり、非即時決済では未対応なので、入金確定には絶対に使用しない。初期の stage デバッグでは公式推奨どおり省略して ECPay 側エラーを見えるようにし、本番UXが必要になった段階で追加する。

## CheckMacValue

全方位金流 AIO 用の公式手順をそのまま実装する。

1. `CheckMacValue` 自身を除く送信項目をキーの昇順で並べる。
2. `&` で連結する。
3. 先頭に `HashKey=...&`、末尾に `&HashIV=...` を付ける。
4. ECPay の URL encode 変換表どおりに encode する。
5. 全体を小文字化する。
6. SHA-256 → 大文字16進数にする。

Python 標準ライブラリだけで実装し、公式資料の既知ベクトル（期待値 `6C51C9E6...B840`）を unit test に固定する。受信値との比較は `secrets.compare_digest()` を使う。

## ReturnURL の処理

`/webhooks/ecpay/payment/` は ECPay からの外部 POST なので Django CSRF は exempt にする代わりに、次をすべて満たすまで状態を変えない。

1. form-data を受信し、必須項目を検証
2. `MerchantID` が設定値と一致
3. CheckMacValue が一致
4. `MerchantTradeNo` で Payment を特定し `select_for_update()`
5. `TradeAmt == Payment.amount == Order.final_total`
6. Payment.currency が TWD
7. `RtnCode == 1`
8. production では `SimulatePaid != 1`
9. `TradeNo` の衝突がない
10. 既に confirmed なら副作用を再実行せず成功応答

transaction commit 後に既存 outbox から支払い完了通知を1回だけ送る。DB commit が成功した後だけ、本文が厳密に `1|OK` の `text/plain` response を返す。不正通知や一時的DB障害では成功応答を返さず、ECPay の再送に任せる。

ユーザー戻り画面は Payment の現在状態をGET表示するだけにし、callback payload を根拠に直接 confirmed へ変更しない。

## 照会・障害復旧

公式 `QueryTradeInfo/V5` を次に使う。

- ReturnURL を受けた取引の追加照合
- credit / TWQR で通知が来ない場合、決済開始10分後、必要ならさらに10分後または40分後に再照会
- 管理画面の「ECPay状態を照会」操作
- 日次 reconciliation

照会APIは高速連打すると 403 になるため、outbox と backoff を使い、ページ再読込のたびには呼ばない。照会応答でも MerchantTradeNo、MerchantID、TradeAmt、CheckMacValue を検証する。

## 返金

初期リリースは ECPay 管理画面で返金し、その結果を本サイトの Payment へ監査記録する。自動化は信用卡 `DoAction` の状態遷移を正しく扱える第2段階にする。

- 已授權: `Action=N`（放棄）
- 要關帳: 全額は `E` 後 `N`、一部は `R`
- 已關帳: `Action=R`
- 分割・紅利折抵は原則全額返金

本番 DoAction に stage endpoint はないため、mock と契約テストだけで本番APIを有効にせず、運用承認を別途設ける。

## 必須テスト

- 公式 CheckMacValue 既知ベクトル
- field 順序が変わっても同じ検査値
- 1文字改ざん、MerchantID違い、金額違いを拒否
- production の `SimulatePaid=1` を拒否
- 同一 callback の複数回送信で Payment / 通知が1件だけ
- 同時 callback でも confirmed が1件だけ
- ReturnURL とユーザー戻りの順序が逆でも正しい
- ECPayエラー、timeout、DB失敗時に誤って paid にしない
- stage checkout fields と endpoint
- 商品名長制限・特殊文字・TWD整数・MerchantTradeNo 20文字制限
- 支払い済み／取消済み／期限切れリンクから再決済できない
- CSP を enforce する場合、`form-action` に stage / production の ECPay origin を明示

## 導入順序

1. ECPay契約と本番 MerchantID / HashKey / HashIV の取得
2. CheckMacValue と hosted-form provider の実装
3. migration、ReturnURL、ユーザー戻り画面、監査ログの実装
4. unit / concurrency / production-mode tests
5. stage で 3D Secure、成功、失敗、離脱、callback再送を実機確認
6. QueryTradeInfo reconciliation を追加
7. `PaymentMethod(code=credit_card, provider=ecpay)` を stage だけで有効化
8. 少額の本番 end-to-end と ECPay 管理画面・DB・LINE通知の突合
9. 運用手順と監視を確認して本番公開
