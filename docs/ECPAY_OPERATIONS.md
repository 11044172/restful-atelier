# ECPay AIO 運用・STAGE検証

## 構成と入金確定

- 顧客支払いページ: `/pay/<signed-token>/`
- 通常入口: `payment_variant=standard`（ECPayへは`ChoosePayment=ALL`）
- 分割入口: `payment_variant=installment`（ECPayへは`ChoosePayment=Credit`）
- ECPay遷移: hidden formを同一画面でAioCheckOut V5へPOST
- ReturnURL: `/payments/ecpay/callback/`
- 顧客戻り画面: `/payments/ecpay/return/<signed-token>/`

顧客から受け付ける選択値は`standard`と`installment`だけです。ChoosePayment、IgnorePayment、CreditInstallment、ChooseSubPayment、TotalAmountその他のECPayパラメータは、Djangoの固定allowlist、環境変数、DBの注文金額から生成します。

顧客戻り画面はPaymentの現在状態を表示するだけです。ブラウザの戻りや表示内容を入金根拠にはせず、CheckMacValue、MerchantID、MerchantTradeNo、TradeAmt、RtnCode、PaymentTypeを検証したReturnURLだけがPaymentとOrderを支払済みにします。Productionの`SimulatePaid=1`は入金確定しません。同じcallbackは冪等処理し、既存outboxのdedupe制約で支払完了LINE通知を重複送信しません。

## Render設定

STAGEとProductionはRender Environmentを分け、秘密値そのものはREADME、Git、Render Blueprintへ書きません。

```text
ECPAY_ENV=stage
ECPAY_MERCHANT_ID=<STAGEの値>
ECPAY_HASH_KEY=<STAGEの値>
ECPAY_HASH_IV=<STAGEの値>
ECPAY_STANDARD_ENABLED=true
ECPAY_INSTALLMENT_ENABLED=false
ECPAY_CREDIT_INSTALLMENTS=3,6,12,18,24
ECPAY_IGNORE_PAYMENT=WebATM#ATM#CVS#BARCODE#BNPL#WeiXin
```

分割を利用する環境だけ`ECPAY_INSTALLMENT_ENABLED=true`にし、`ECPAY_CREDIT_INSTALLMENTS`はその加盟店で実際に開通済みの期数だけにします。AIO V5の通常分割で許可する値は`3,6,12,18,24`です。空、未対応値、分割無効時は顧客画面に分割ボタンを出しません。

`ECPAY_IGNORE_PAYMENT`は`WebATM`、`ATM`、`CVS`、`BARCODE`、`BNPL`、`WeiXin`だけを受け付けます。`Credit`、`ApplePay`、`TWQR`、`DigitalPayment`は指定できません。iPASS MONEYと街口支付はDigitalPaymentに属するためです。

deploy時に`python manage.py migrate`を実行します。今回のvariant／callback追加情報は既存Paymentの`provider_metadata`を使うため新規DB migrationはありません。管理画面の「付款方式設定」で信用卡の`provider=ecpay`と`enabled=True`を確認します。既存PaymentMethodと過去Paymentは削除しません。

ReturnURLは外部から到達可能なHTTPS URLである必要があります。正式originが`https://restful-atelier.com`の場合、ECPayへ送るURLは`https://restful-atelier.com/payments/ecpay/callback/`です。Cloudflare等でこのURLへの`application/x-www-form-urlencoded` POSTを遮断しないでください。

## 送信パラメータ

共通: `MerchantID`、`MerchantTradeNo`、`MerchantTradeDate`、`PaymentType=aio`、`TotalAmount`、`TradeDesc`、`ItemName`、`ReturnURL`、`EncryptType=1`、`ClientBackURL`、`NeedExtraPaidInfo`、`CheckMacValue`。

通常入口では`ChoosePayment=ALL`、`IgnorePayment=WebATM#ATM#CVS#BARCODE#BNPL#WeiXin`、`NeedExtraPaidInfo=N`を追加し、`CreditInstallment`は送信しません。ECPay画面には、そのSTAGE／本番加盟店で契約・開通済みかつ金額・端末条件を満たす方式だけが表示されます。

分割入口では`ChoosePayment=Credit`、`CreditInstallment=<カンマ区切りの開通済み期数>`、`NeedExtraPaidInfo=Y`を追加し、`IgnorePayment`は送信しません。`NeedExtraPaidInfo=Y`によりcallback追加項目`stage`（実分割回数）を受け取り、作成時に保存した許可期数と一致する場合だけ自動確定します。未開通で一括へfallbackした場合の`stage=0`／欠落、未許可期数、別PaymentTypeは監査待ちとしてOrderをpaidにしません。

## callback allowlistと表示名

ECPay AIO V5公式の「回覆付款方式一覽表」にある、今回の通常入口で到達可能な次のraw値だけを自動確定します。

| PaymentType | 管理画面表示 |
| --- | --- |
| `Credit_CreditCard` | 信用卡／Apple Pay |
| `TWQR_OPAY` | TWQR |
| `DigitalPayment_IPASS` | iPASS MONEY |
| `DigitalPayment_Jkopay` | 街口支付 |

分割Paymentは`Credit_CreditCard`かつ許可済み`stage`の場合に「信用卡分期付款（6期）」のように表示します。

AIO V5の公式reply表は`Credit_CreditCard`を「信用卡またはApple Mobile Pay」と定義しており、Apple Pay専用の別callback値を掲載していません。銀聯卡と綠界Payについても同表に推測可能な専用raw値がないため、架空のPaymentTypeはallowlistへ追加していません。STAGEで実際に返るraw callbackはAdmin／安全化metadataで確認します。もし公式表と異なる値が返れば、自動確定せず`payment_review_required`監査記録を残すので、ECPay管理画面・最新公式表・サポート回答を照合してからコードallowlistを更新します。

カード番号、安全碼、`card6no`、`card4no`、完全なカード情報は保存しません。

## 金額条件

TWQR公式条件はNT$6～49,999です。通常入口の注文が範囲外でも、信用卡等の他方式が利用できるため通常ECPayボタンは表示します。顧客向け補足からTWQRを外し、仮に範囲外TWQR callbackが来た場合は自動確定せず監査待ちにします。信用卡、Apple Pay、DigitalPayment等はAIO各ページに一律の追加最低・最高額が明記されていないため、DB由来の正整数TWDを送り、加盟店契約・ECPay画面の判定に従います。

## 再試行

ECPay画面へ遷移するたびに新しいPaymentと20文字以内のMerchantTradeNoを作成します。同一variantの再試行、standard／installment変更、失敗後の再試行で再利用しません。前のpending／awaiting Paymentはcancelledにします。callbackがすでに届いて監査待ち（TradeNo保存済み）のPaymentがある場合は、二重課金を避けるため顧客再試行を止め、Adminでの照合を要求します。

## STAGE手動確認

1. Renderへ上記8個のECPay環境変数を設定し、deployを完了します。最初は分割をfalseにします。
2. Adminの付款方式設定で信用卡の`provider=ecpay`と`enabled=True`を確認します。
3. LINE Login済み顧客として注文し、Adminで運費を入力して「確定運費並寄送付款通知」を実行します。
4. LINE Flex Messageに従来どおり「前往付款」「取消訂單」の2ボタンだけがあることを確認します。
5. 支払いリンクで注文番号、商品、商品小計、送料、最終支払額、規約同意、「使用 ECPay 付款」を確認します。台湾Pay／銀行轉帳／PayPal／信用卡の旧radio一覧がないことを確認します。
6. 通常入口を押し、ECPay画面で信用卡、銀聯卡、Apple Pay、TWQR、iPASS MONEY、街口支付、綠界Payのうち加盟店で開通済み方式が表示されることを記録します。特に変更前に見えていたiPASS MONEY、街口支付、綠界Payをスクリーンショットで照合します。
7. STAGEで利用可能な各方式を決済し、ReturnURLが`1|OK`、Paymentがconfirmed、Orderがpaid、LINE支払完了通知が1回だけであることを確認します。Apple Payは対応端末・ブラウザを使います。
8. AdminのPaymentでvariant、raw PaymentType、表示名、実分割回数、MerchantTradeNo、ECPay TradeNo、金額、status、paid_atを記録します。銀聯卡／Apple Pay／綠界Payはraw値が公式reply表の表現と一致するか必ず記録します。
9. 本番加盟店で分割期数を開通後、Renderの期数を実契約だけに絞り`ECPAY_INSTALLMENT_ENABLED=true`へ変更します。分割ボタンからECPayへ進み、表示期数、callbackの`stage`、Admin表示を照合します。
10. 同一callbackを再送してもPayment、Order、在庫、LINE通知が重複しないことを確認します。
11. NT$5とNT$50,000の注文で通常入口は残り、補足にTWQRが出ず、ECPay側でも利用不可となることを確認します。

STAGEでテストできる方式・表示はSTAGE加盟店の開通状態に依存します。銀聯カードはECPayの公式注意事項上STAGEで提供されない場合があり、分割は加盟店で開通した期数だけ、Apple Payは対応端末だけ、綠界Pay／DigitalPayment／TWQRは各サービスの開通が必要です。未開通方式をコードだけで強制表示・テストすることはできません。

## Production切替

STAGE記録とECPay管理画面の照合が完了してから、Renderの`ECPAY_ENV=production`と本番MerchantID／HashKey／HashIVへ切り替えます。通常／分割のenableと分割期数も本番契約に合わせて独立設定します。本番では信用卡、銀聯卡、Apple Pay、TWQR、iPASS MONEY、街口支付、綠界Pay、希望する分割期数をそれぞれ申請・開通し、実際に契約済みの方式だけがECPay画面へ表示されることを確認します。

少額本番決済でECPay管理画面、Payment、Order、LINE通知を突き合わせます。`SimulatePaid=1`は本番入金として扱いません。

## 障害確認

- callbackがHTTP 400: CheckMacValue、MerchantID、MerchantTradeNo、TradeAmt、必須項目不一致を確認します。秘密鍵やcallback全体はログ出力しません。
- callbackがHTTP 503: ECPay環境変数不足または`ECPAY_ENV`不正です。
- Paymentが「等待確認」でTradeNoあり: `provider_metadata.review_required`とOrder auditの`payment_review_required`を確認し、ECPay管理画面で入金・方式・分割回数を照合します。顧客には再決済させません。
- 顧客画面が「確認中」: ReturnURL到着遅延があり得ます。ブラウザ表示だけで手動確定しません。
- ECPayが通知を再送する: 正常処理時の応答が厳密に`1|OK`か確認します。

## 公式資料

- [全方位金流付款](https://developers.ecpay.com.tw/2864/)
- [產生訂單](https://developers.ecpay.com.tw/2862/)
- [信用卡一次付清・銀聯](https://developers.ecpay.com.tw/2866/)
- [信用卡分期付款](https://developers.ecpay.com.tw/2870/)
- [Apple Pay](https://developers.ecpay.com.tw/7328/)
- [TWQR](https://developers.ecpay.com.tw/36991/)
- [付款方式一覽表](https://developers.ecpay.com.tw/5679/)
- [回覆付款方式一覽表](https://developers.ecpay.com.tw/5686/)
- [CheckMacValue](https://developers.ecpay.com.tw/2902/)
- [付款結果通知](https://developers.ecpay.com.tw/2878/)
- [付款結果額外回傳參數](https://developers.ecpay.com.tw/5675/)
