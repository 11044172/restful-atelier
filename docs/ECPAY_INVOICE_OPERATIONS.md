# ECPay B2C 電子發票運用手順

最終更新: 2026-10-03

## 安全設計

- 初期値は`ECPAY_INVOICE_ENABLED=false`です。deployだけで正式発票は発行されません。
- 金流と発票のcredential、環境、transactionを分離します。
- 入金確定transaction内ではInvoice pendingレコードを一度だけ作り、commit後にAPIを呼びます。
- 発票APIが失敗してもPayment=confirmed／Order=paidを維持し、ReturnURLへは正常な`1|OK`を返します。
- OrderごとのOneToOne制約と一意な30文字以内RelateNumber、`issuing` lock、再試行前の`GetIssue`照会で二重発行を防ぎます。
- provider metadataにはRtnCode／RtnMsg等の最小摘要だけを保存し、credentialや暗号化payload全文を保存しません。

## Environment Variables

```text
ECPAY_INVOICE_ENABLED=false
ECPAY_INVOICE_ENV=stage
ECPAY_INVOICE_MERCHANT_ID=
ECPAY_INVOICE_HASH_KEY=
ECPAY_INVOICE_HASH_IV=
ECPAY_INVOICE_TAX_TYPE=
ECPAY_INVOICE_INV_TYPE=
ECPAY_INVOICE_VAT=
ECPAY_INVOICE_TIMEOUT=10
```

- `ECPAY_INVOICE_ENV`: `stage`または`production`
- credential: ECPay電子發票契約から取得します。金流credentialと同一だと推測しません。
- `TAX_TYPE`: 課稅類別。事業者・商品税務に基づいて設定します。
- `INV_TYPE`: 字軌類別（例: 一般税額07）。ECPay後台の字軌と一致させます。
- `VAT`: 商品単価が含税なら1、未税なら0です。本システムの注文価格snapshotは含税総額として扱うため、自動発行は明示的な`VAT=1`だけをサポートします。
- `TIMEOUT`: 0超30秒以下。

STAGEは未入力の税項目へ公式一般テスト値（TaxType=1、InvType=07、vat=1）を利用できます。Productionは3項目すべて明示必須で、未設定ならInvoiceはpendingのまま「電子發票未設定」と表示されます。

現在は安全のため一般税額・応税・含税込み（07／1／1）だけを自動発行対象とします。零税率、免税、特種税額、混合税率は通関方式、零税率理由、品目別税区分等の会計判断が追加で必要なため、`review_required`に止めます。

## Checkoutとsnapshot

注文時に次のいずれかを保存します。

- 個人電子發票: `CarrierType=1`、`CarrierNum=""`、`Print=0`
- 手機條碼: `CarrierType=3`、`CarrierNum=/`から始まる8文字、`Print=0`
- 公司用電子發票: 8桁統一編號と会社名、`CarrierType=1`、`Print=0`

Email、数字化した電話、carrier、統一編號、会社名、注文作成時の環境／税設定を`OrderInvoiceProfile`へsnapshot保存します。商品明細はOrderItemの名称・単価・数量・小計snapshotを使い、現在のProduct価格は参照しません。送料は「運費」1明細です。明細合計、送料、SalesAmount、Order.final_totalが一致しない場合は正式発行しません。

手機條碼はサーバー側で形式検証し、発行直前に`CheckBarcode`も補助利用します。ECPay／財政部の検証API障害は公式注意事項に従って唯一の拒否根拠にせず、形式が正しい場合はIssueを続行します。明確に`IsExist=N`なら人工確認へ止めます。

## STAGE確認

1. ECPay電子發票後台でSTAGE用MerchantID／HashKey／HashIVを取得します。
2. 上記をRenderへ設定し、`ECPAY_INVOICE_ENV=stage`、最後に`ECPAY_INVOICE_ENABLED=true`へします。
3. 個人、手機條碼、公司用の3注文を作成します。
4. 決済前はInvoiceが作られないことを確認します。
5. ReturnURLで入金確定後、Invoice=issued、InvoiceNo、InvoiceDate、RandomNumberを確認します。
6. ECPay STAGE後台のRelateNumber／金額／明細／載具／統編とDjango Adminを照合します。
7. API失敗をmockまたは一時的な無効credentialで確認し、Payment／OrderがpaidのままInvoiceだけfailed/pendingになることを確認します。
8. Admin「重新嘗試開立電子發票」または`python manage.py retry_pending_invoices`で安全に復旧します。

## Production開始前にECPay後台で確認する値

- 電子發票契約が正式環境で有効
- 電子發票専用MerchantID／HashKey／HashIV
- 字軌と配號が設定・有効化済み
- 課稅類別`TaxType`
- 字軌類別`InvType`
- 商品価格が含税か未税か（`vat`）
- ECPay側のEmail発票通知設定（二重通知防止。本実装は発票LINE通知を追加していません）
- 公司統編発票を綠界載具へ保存する運用が契約・会計要件に合うこと

STAGE記録を照合後、Renderへ本番invoice credentialと税設定を入力し、`ECPAY_INVOICE_ENV=production`へ変更します。最初は`ENABLED=false`のままdeployし、設定確認後にだけ`true`へします。

## 失敗・再試行

- `pending/configuration`: 設定不足。設定を直してAdminまたはcommandで再試行します。
- `failed`: timeout、connection、ECPay API error。再試行前に同じRelateNumberを`GetIssue`照会します。
- `review_required`: 金額、snapshot、税、載具等の不整合。原因を会計・注文情報と照合してから対応します。
- `issuing`: 通常は処理中。15分以上残ったものだけmanagement commandが回収対象にします。
- `issued`／`voided`: Issue APIを再実行しません。

```bash
python manage.py retry_pending_invoices
python manage.py retry_pending_invoices --invoice-id 123
```

## 退款・取消・作廢

注文キャンセルや返金から発票の作廢／折讓を自動実行しません。発行済み注文をrefund状態にするとAdminへ警告を表示します。作廢、折讓、退款は取引時期・申告状況・全額／一部返金で会計処理が変わるため、ECPay後台と会計担当者の判断で処理し、結果を照合してください。

## ロールバック

緊急停止は`ECPAY_INVOICE_ENABLED=false`です。金流は継続し、Invoiceはpendingに残ります。復旧後に同じRelateNumberで照会・再試行します。migration 0009は既存Order／Paymentを更新せず、新規テーブルだけを追加するreversible migrationです。ただし発票履歴がある環境では監査・税務記録を保持するためreverseしないでください。

## 公式資料

- [一般開立發票](https://developers.ecpay.com.tw/53662/)
- [AES加密](https://developers.ecpay.com.tw/49105/)
- [手機條碼驗證](https://developers.ecpay.com.tw/7886/)
- [查詢發票明細](https://developers.ecpay.com.tw/49903/)
- [線上開立折讓](https://developers.ecpay.com.tw/15391/)
