# 正式公開 Runbook

現在のRender URL、checkout、`SEO_INDEX_ENABLED=False`を維持したまま準備する。正式domain DNSとnoindex解除は最後に行う。

1. 正式商品名、SKU、価格、在庫、説明、alt、画像、予約上限・交付予定をAdminへ入力し、TEST／仮画像を一覧確認・非公開化する。
2. Site Settingsへ事業者名、責任者、電話、Email、営業所、返品窓口、営業時間、個資窓口を入力する。
3. 6政策を台湾の実際の商材、配送、例外、窓口に合わせて専門家・事業者が確認し、version、生效日、`legal_reviewed`を更新する。
4. staging用とproduction用のDB、LINE channel、R2 bucket/prefix、SMTP、payment credential、domainを分離する。Renderで `DEPLOYMENT_ENV=production` と `CREDENTIAL_SET_NAME=production` を設定する。
5. LINE callbackを `{正式origin}/auth/line/callback/`、webhookを `{正式origin}/webhooks/line/messaging/` に登録し、署名付きVerifyを行う。SMTP、R2、Turnstile、手動付款を疎通確認する。
6. Render Background Workerを `python manage.py process_notification_outbox --limit 100000 --poll-seconds 2` で追加し、料金を承認後 `OUTBOX_WORKER_CONFIGURED=True` にする。
7. `python manage.py check_launch_readiness --external --production` を実行し、FAILを0にする。WARNも根拠と承認者を記録する。
8. PostgreSQLとR2のbackupを取得し、restore testの最終成功日を確認する。
9. 正式domainをRenderへ追加し、`ALLOWED_HOSTS`、`CSRF_TRUSTED_ORIGINS`、`CANONICAL_ORIGIN`、LINE callback/webhookを同時に切り替える。DNS変更は事業者承認後に行う。
10. 実スマートフォンで少額production注文を1件だけ実施する: LINE Login→cart→checkout→運費確定→LINE/Email→支払／取消表示→付款確認→出貨通知。実課金の有無と退款方法を事前に決める。
11. Desktop、tablet、375px mobileで主要ページ、404、console、画像、modal、keyboardを再確認する。
12. 最後に、事業者の明示承認後だけ `SEO_INDEX_ENABLED=True` にする。robots、sitemap、canonicalを確認する。

RollbackはGit release、DB migration、credential、DNS、Outboxを別々に扱う。履歴データを削除するrollbackを行わない。migrationに問題がある場合は新しい修正migrationを優先する。
