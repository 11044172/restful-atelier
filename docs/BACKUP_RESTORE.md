# Backup / Restore

## 目標

- 推奨RPO: PostgreSQL 24時間以内、R2画像24時間以内。正式受注件数に応じて短縮する。
- 推奨RTO: 4時間。担当者、連絡先、Render／Cloudflareの権限保有者を事業者側で確定する。
- DBと画像は同じ復元時点ラベルで管理し、注文DBの画像keyとR2 objectを復元後に照合する。

## Backup

1. Render PostgreSQLの契約planで自動backup、保持日数、Point-in-Time Recovery可否をDashboardで確認する。コードから有効化済みとは扱わない。
2. 定期的にRenderの正式手順で暗号化した論理dumpを取得し、productionとは別権限の保管先へ保存する。dump名にUTC時刻、DB、Git commit、migration leafを記録する。
3. Cloudflare R2でobject versioning／ライフサイクル／複製可否を契約と要件に合わせて設定する。資格情報をbackupへ同梱しない。
4. `python manage.py manage_test_data` とOutbox dead件数を記録してから重要releaseを行う。

## Restore test

本番へ上書きせず、隔離した新DBと新bucket/prefixへ復元する。

1. Web／workerを隔離環境へ接続し、`python manage.py migrate --plan` が想定どおりか確認する。
2. `python manage.py verify_restore_integrity` でDB制約と注文明細を確認する。
3. R2資格情報を隔離環境だけへ設定し、`python manage.py verify_restore_integrity --storage` で画像参照を確認する。
4. 注文件数、付款合計、最新注文番号、商品在庫、Outbox、画像件数をbackup時記録と照合する。
5. 代表商品画像、政策ページ、注文→送料→付款→出貨の非送信testを確認する。
6. 実施日、所要時間、欠落、実RPO/RTO、改善項目を記録する。少なくとも四半期ごとと大きなmigration前に実施する。

## 実障害復元

復元対象時刻を確定し、受注停止ではなく必要に応じて管理者が顧客へ障害を通知する。DB復元とR2復元を別々に完了扱いにせず、整合性検査後にWeb、次にOutbox workerを起動する。未送信Outboxは重複防止keyを維持したまま再開し、LINE retry keyが24時間を超えた通知は自動再送せず人が重複リスクを判断する。
