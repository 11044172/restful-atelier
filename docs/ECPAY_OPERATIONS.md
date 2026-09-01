# ECPay AIO 運用・STAGE検証

## 構成

- 顧客支払いページ: `/pay/<signed-token>/`
- ECPay遷移: 顧客支払いページのPOST処理がhidden formを生成し、AioCheckOut V5へPOST
- ReturnURL: `/payments/ecpay/callback/`
- 顧客戻り画面: `/payments/ecpay/return/<signed-token>/`

顧客戻り画面はPaymentの現在状態を表示するだけです。ブラウザの戻りや表示内容を入金根拠にはせず、CheckMacValue、MerchantID、MerchantTradeNo、TradeAmt、RtnCodeを検証したReturnURLだけがPaymentとOrderを支払済みにします。

## Render STAGE設定

Render DashboardのEnvironmentへ次を設定します。秘密値そのものはREADME、Git、Render Blueprintへ書きません。

```text
ECPAY_ENV=stage
ECPAY_MERCHANT_ID=<STAGEの値>
ECPAY_HASH_KEY=<STAGEの値>
ECPAY_HASH_IV=<STAGEの値>
```

deploy時に`python manage.py migrate`を実行します。管理画面の「付款方式設定」で信用卡を開き、`provider=ecpay`を確認して`enabled`を有効にします。設定不足時は支払いページから信用卡が除外されます。

ReturnURLは外部から到達可能なHTTPS URLである必要があります。現在の正式originが`https://restful-atelier.com`の場合、ECPayへ送るURLは`https://restful-atelier.com/payments/ecpay/callback/`です。Cloudflare等を利用する場合、このURLへのPOST、`application/x-www-form-urlencoded`、ECPayからの通信を遮断しないでください。

## STAGE手動確認

1. Renderへ4個のECPay環境変数を設定し、deployとmigrationを完了します。
2. Adminの付款方式設定で信用卡の`provider=ecpay`と`enabled=True`を確認します。
3. LINE Login済みの顧客として商品をカートへ入れ、注文を送信します。
4. Adminの注文詳細で運費を入力し、「確定運費並寄送付款通知」を実行します。
5. LINE Flex Messageで商品小計、運費、合計、付款、取消の各表示を確認します。
6. LINEの付款ボタンから`/pay/<token>/`を開き、注文番号とDB由来の最終金額を確認します。
7. 信用卡を選び、最終条件へ同意して「使用 ECPay 安全付款」を押します。
8. 同じLINE内ブラウザでECPay STAGEへ遷移することを確認し、ECPay公式のSTAGEテストカード情報で決済します。
9. ECPay画面の「返回商店」から戻り、確認中または確認済み表示を確認します。ReturnURLとブラウザ戻りの順序は保証されないため、直後は確認中でも正常です。
10. AdminのPaymentでstatus、MerchantTradeNo、ECPay TradeNo、paid_atを、注文でstatus=`paid`と最終金額を確認します。
11. LINEで「已確認收到您的付款…」の通知が1回だけ届くことを確認します。
12. 同一callbackを再送しても、Payment、Order、在庫、LINE通知が重複しないことをログとAdminで確認します。

## Production切替

STAGEの実機確認とECPay管理画面との照合が完了してから、Renderの値だけを変更します。

```text
ECPAY_ENV=production
ECPAY_MERCHANT_ID=<本番値>
ECPAY_HASH_KEY=<本番値>
ECPAY_HASH_IV=<本番値>
```

コード変更は不要です。本番では`SimulatePaid=1`の通知を支払済みにしません。切替後は少額の本番決済で、ECPay管理画面、Payment、Order、LINE通知を突き合わせてください。

## 障害確認

- callbackがHTTP 400: CheckMacValue、MerchantID、MerchantTradeNo、TradeAmtの不一致をアプリログで確認します。秘密鍵やcallback全体はログ出力しません。
- callbackがHTTP 503: ECPay環境変数が不足または`ECPAY_ENV`が不正です。
- ECPayが通知を再送する: 正常処理時の応答本文が厳密に`1|OK`であることを確認します。
- 顧客画面が「確認中」: ReturnURLの到着遅延があり得ます。ECPay管理画面とアプリログを確認し、ブラウザ表示だけで手動確定しません。
- 決済失敗後の再試行: 失敗Paymentは`failed`となり、次回開始時に新しい20文字以内のMerchantTradeNoを持つPaymentを作成します。
