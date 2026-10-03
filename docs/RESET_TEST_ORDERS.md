# 正式運用開始前のテスト注文リセット

この手順は、実顧客注文・実決済・本番電子発票がまだ存在しない正式運用開始前に限って使用する。
管理画面には機能を追加せず、reset_test_orders management commandだけを提供する。正式運用開始後は使用しない。

## このリポジトリでの対象

render.yaml上のWeb Serviceはrestfull、PostgreSQLはrestfull-db（Singapore、PostgreSQL 17、
basic-256mb）である。Webとprocess_notification_outbox workerは同じstart.shから起動する。
本番実行前にはSite Settingsのcheckout_enabledをOFFにし、新規注文が作成されない状態にする。

コマンドが削除するのは全Orderと、それに紐づく次のデータである。

- OrderItem
- Payment
- Invoice
- OrderInvoiceProfile
- LineNotification
- NotificationOutbox
- OrderAuditLog
- PolicyAcceptance

Product、ProductCategory、ProductImage、PaymentMethod、LineCustomer、管理者、
Site Settings、コンテンツその他のマスタは削除しない。DB sequenceと注文番号仕様も変更しない。

## 在庫復元条件

次の全条件を満たす明細だけを商品ごとに合算する。

1. Order.inventory_reserved=True
2. Order.inventory_released=False
3. OrderItem.product_idが存在する
4. OrderItem.stock_was_reserved=True

復元量はProduct.stock + Sum(OrderItem.quantity)である。注文、明細、商品と全関連行を
トランザクション中にロックし、在庫復元・関連削除・実行後検証を一つのtransaction.atomic()で行う。
途中の例外は全変更をロールバックする。通常業務用のcancel_order()、モデルsave()、
通知enqueue、LINE、Email、ECPay APIは呼ばない。

## 1. バックアップ（本実行の必須条件）

1. Render Dashboardでrestfull-dbを開き、Recoveryページへ進む。
2. Create exportを実行する。
3. 完了した.dir.tar.gzをダウンロードし、作成時刻、Git commit、migration leaf
   （python manage.py showmigrations --planの末尾）と一緒に安全な場所へ保存する。
4. 同じRecoveryページでPITRの利用可能期間を確認する。Renderのpaid PostgreSQLではPITRが提供されるが、
   実際に選択可能な復元時刻を画面で確認する。
5. exportの作成時刻・ファイルサイズ・ダウンロード完了を作業記録へ残す。

Render公式の現行手順:
https://render.com/docs/postgresql-backups

バックアップの存在と復元方法を確認するまで--backup-confirmedを付けてはならない。

## 2. 書き込み停止とDry Run

1. Site Settingsでcheckout_enabledをOFFにする。
2. RenderのrestfullサービスのShellを開く。
3. デプロイ済みcommitがこのコマンドを含むことを確認する。
4. 次を実行する（オプションなしは常に読み取り専用）。

    python manage.py reset_test_orders

次を確認する。

- All ordersとOrders to deleteが現在の全テスト注文件数に一致する。
- Payment、Invoice、Invoice profile、LINE notification、Outbox、Audit log、
  Policy acceptanceの件数が想定と一致する。
- 各商品のCurrent + Restore = Afterが、注文テスト前の正しい在庫になる。
- Orders with already released inventoryは再加算されていない。
- 予約注文などstock_was_reserved=Falseの明細は復元量に含まれていない。
- Safety checks: OKである。
- 最後がDRY RUN ONLY / No database changes have been made.である。

Safety checks: FAILEDの場合は実行しない。特にproduct NULL、フラグ不整合、異常quantity、
未知の関連モデル、変更された削除制約、本番ECPay実取引候補、本番Invoice送信候補、
処理中Outboxは、原因とデータを個別に調査する。

## 3. 本実行

Dry Run出力を保存してレビューし、バックアップを確認した同じ作業者が、次の完全なコマンドを入力する。

    python manage.py reset_test_orders --execute --confirm RESET-TEST-ORDERS --backup-confirmed

確認文字列またはバックアップ確認フラグがなければ実行を拒否する。実行中は管理画面から注文・支払・
請求書・通知を操作しない。現在の構成では通知workerも同一サービス内にいるため、コマンドはOutbox行を
ロックしてworkerのskip_locked対象から外し、すでにPROCESSINGのjobがあれば停止する。

## 4. 実行後の確認

成功時にはコマンド自身が次を0件として検証し、復元対象商品の最終stockを表示する。

    Orders: 0
    OrderItems: 0
    Payments linked to deleted orders: 0
    Invoices linked to deleted orders: 0
    Invoice profiles linked to deleted orders: 0
    Notifications linked to deleted orders: 0
    Outbox jobs linked to deleted orders: 0
    Audit logs linked to deleted orders: 0
    Policy acceptances linked to deleted orders: 0
    Reset completed successfully.
    No external notifications were sent.

念のため、直後にもう一度Dry Runを実行する。

    python manage.py reset_test_orders

All orders: 0、全関連件数0、Inventory restoration: (none)を確認する。商品管理画面でもDry Runで
示された各商品の最終stockを照合する。問題がなければ正式運用開始時にcheckout_enabledをONへ戻す。

## 5. 問題時の復元

コマンド内のエラーならtransaction.atomic()により在庫と削除は両方ロールバックされる。
再実行せず、エラー全文とDry Run出力を保存して原因を調査する。

commit後に誤りが判明した場合は、Renderの元DBへ上書き復元しない。

1. restfullの書き込みを停止し、checkout_enabledをOFFのままにする。
2. restfull-dbのRecoveryページで、リセット直前（現在時刻の10分以内は指定不可）の時刻から
   Restore Databaseを実行し、新しい隔離DBを作成する。
3. 新DBがAvailableになったら、そのInfoページのPSQL commandで接続し、
   python manage.py verify_restore_integrity相当の整合性、注文件数、商品在庫、関連件数を確認する。
4. 検証済み新DBをWeb ServiceのDATABASE_URL参照先にする。Blueprint管理を継続する場合は
   render.yamlのfromDatabase.nameも新DB名へ更新してからデプロイする。
5. Webとworkerが新DBだけを参照していることを確認してから受付を再開する。
6. 元DBは即時削除せず、切替確認が完了するまで保持する。

Dashboard exportから復元する場合も、空の新DBだけを対象にし、公式手順どおり展開後に
pg_restore --format=directoryを使用する。重要データのある既存DBに--clean付きで直接復元しない。
