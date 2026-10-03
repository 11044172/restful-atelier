# ECPay AIO V5 本番運用手順

最終更新: 2026-10-03

## 入金確定の原則

- 顧客支払いページ: `/pay/<signed-token>/`
- ECPay ReturnURL: `/payments/ecpay/callback/`
- 顧客戻り画面: `/payments/ecpay/return/<signed-token>/`
- 入金のsource of truthは、署名検証済みServer-to-Server ReturnURLだけです。
- `OrderResultURL`、`ClientBackURL`、戻り画面、success表示ではPayment／Orderをpaidにしません。
- callbackでは`CheckMacValue`、`MerchantID`、`MerchantTradeNo`、`TradeAmt`、`RtnCode`、`PaymentType`、`SimulatePaid`を検証します。
- `RtnCode=1`だけを成功候補とし、Productionの`SimulatePaid=1`は失敗扱いです。
- 同一callbackはDB lock・一意制約・通知dedupeにより冪等です。正常受信時は本文を厳密に`1|OK`で返します。

callback全文、HashKey、HashIV、カード番号、安全碼、`card6no`、`card4no`は保存・ログ出力しません。保存するcallback metadataは監査に必要なallowlist項目だけです。

## Render Environment Variables

秘密値はRenderだけへ設定し、Git、README、HTML、JavaScript、DB、エラー応答へ書きません。

```text
ECPAY_ENV=production
ECPAY_MERCHANT_ID=<本番金流MerchantID>
ECPAY_HASH_KEY=<本番金流HashKey>
ECPAY_HASH_IV=<本番金流HashIV>
ECPAY_STANDARD_ENABLED=true
ECPAY_INSTALLMENT_ENABLED=false
ECPAY_CREDIT_INSTALLMENTS=
ECPAY_IGNORE_PAYMENT=ApplePay#BNPL#DigitalPayment
```

STAGEでは`ECPAY_ENV=stage`とSTAGE専用credentialを使用します。金流credentialと電子發票credentialは別管理です。

## 契約済み支払い方法

ECPay後台の「合約及費率」が唯一のsource of truthです。2026-10-03時点で確認済みの方式は次です。

- 信用卡一次付清
- 網路ATM
- ATM櫃員機
- 超商代碼
- 超商條碼
- 全家條碼立即繳
- TWQR行動支付
- 微信支付

未確認の信用卡分期、銀聯卡、Apple Pay、iPASS MONEY、街口支付、綠界Payは有効扱いにしません。`ECPAY_IGNORE_PAYMENT=ApplePay#BNPL#DigitalPayment`で未契約カテゴリを隠し、ECPay側の契約判定も併用します。銀聯卡はAIOのCredit内でECPay側が表示を決めるため、架空のPaymentTypeや独自フラグを送信しません。

分割の契約確認が終わるまで`ECPAY_INSTALLMENT_ENABLED=false`を維持します。開通後だけ期数を`ECPAY_CREDIT_INSTALLMENTS=3,6,12`のように実契約へ限定して有効化します。

## callback PaymentType監査

自動確定するのは公式reply表にあり、現在の契約済みカテゴリに属する値だけです。

- `Credit_CreditCard`
- 現在提供中の`WebATM_*`／`ATM_*`
- `CVS_CVS`、`CVS_OK`、`CVS_FAMILY`、`CVS_HILIFE`、`CVS_IBON`
- `BARCODE_BARCODE`
- `TWQR_OPAY`
- `WeiXin_OPAY`

未知値、未確認の`DigitalPayment_*`、分割入口での別方式、許可外の分割回数、範囲外TWQRは`awaiting_confirmation`／`payment_review_required`へ送り、自動paidにしません。顧客へ再決済させる前にECPay後台でTradeNo・金額・方式を照合します。

## STAGE検証

1. STAGE credentialと`ECPAY_ENV=stage`を設定します。
2. Adminの「付款方式設定」で信用卡の`provider=ecpay`、`enabled=True`を確認します。
3. LINE Login済み顧客で注文し、Adminで送料を確定します。
4. LINEの署名付き付款URLから通常入口を開きます。
5. ECPay画面に契約・STAGE提供対象だけが表示されることを確認します。
6. 成功／失敗／離脱／ATM・超商等の後払いを試します。取号だけではpaidにならず、実入金ReturnURL後だけpaidになることを確認します。
7. callbackの応答`1|OK`、Payment confirmed、Order paid、LINE通知1回を確認します。
8. 同一callbackを再送し、Payment、Order、在庫、LINE通知、電子發票が重複しないことを確認します。
9. 未知PaymentTypeが自動paidにならないことをテストします。

## Production切替と少額smoke test

1. ECPay後台「合約及費率」で上記方式を再確認し、未確認方式を`IgnorePayment`で隠します。
2. Renderへ本番金流credentialを設定し、`ECPAY_ENV=production`へ切り替えます。
3. deploy・migration・health check完了後、テスト商品で少額注文します。
4. LINE Login → 注文 → Adminで送料確定 → LINE付款URL受信を確認します。
5. 実カードで少額決済し、ReturnURL到着後にPayment=confirmed、Order=paidを確認します。
6. LINE支払完了通知が1回だけ届くことを確認します。
7. 電子發票を有効化済みの場合、Invoice=issued、発票番号、ECPay後台の記録を照合します。
8. 同じ付款URL・callbackを再操作しても二重決済／二重発票にならないことを確認します。

## 障害・ロールバック

- callback HTTP 400: 署名、MerchantID、取引番号、金額、必須項目を確認します。秘密値やcallback全文はログに出しません。
- callback HTTP 503: 金流credentialまたは環境設定不足です。
- Paymentが等待確認: `provider_metadata.review_required`とOrderAuditLogを確認し、ECPay後台と照合します。
- 顧客戻り画面が確認中: ReturnURLを待ちます。ブラウザ表示だけで手動paidにしません。
- 金流の緊急停止: `ECPAY_STANDARD_ENABLED=false`と`ECPAY_INSTALLMENT_ENABLED=false`にします。既存入金callbackは受信継続します。
- コードのrollback: 前版へdeployを戻します。migration 0009は新規テーブルだけで既存Order／Paymentを書き換えずreversibleですが、発票履歴が存在する場合は監査保存のため通常はreverseしません。

電子發票の設定・再試行・照合は[ECPAY_INVOICE_OPERATIONS.md](ECPAY_INVOICE_OPERATIONS.md)を参照してください。

## 公式資料

- [全方位金流付款](https://developers.ecpay.com.tw/2864/)
- [付款結果通知](https://developers.ecpay.com.tw/2878/)
- [回覆付款方式一覽表](https://developers.ecpay.com.tw/5686/)
- [CheckMacValue](https://developers.ecpay.com.tw/2902/)
