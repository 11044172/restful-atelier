# RESTFUL ATELIER 運用手順

## 日常運用

### 商品登録・公開

1. Admin「商品」から名称、SKU、価格、通常在庫、説明を入力する。
2. JPEG / PNG / WebP の正式画像と内容を説明するaltを登録し、主画像を1枚指定する。アップロード画像はサーバーで実デコード、メタデータ除去、再エンコードされる。
3. 予約商品は必ず予約上限と予定交付時期を入れる。
4. 販売開始／終了、公開状態を確認する。正式情報が揃うまで公開しない。
5. `python manage.py check_launch_readiness --production` で公開商品エラーがないことを確認する。

在庫変更は商品画面で行う。受注時の通常在庫はtransactionと行ロックで保留され、未払い取消時に一度だけ戻る。注文処理中に帳尻合わせ目的で在庫を直接増減せず、監査ログと該当注文を確認する。

### 注文・送料・付款・出貨

1. 新規注文は「運費確認中」。この時点は無課金で、商品価格のみ確定している。
2. 配送先と商品を確認し、注文画面の運費欄を入力して「運費確定」を実行する。状態が「等待付款」になり、LINE／Emailの支払案内がOutboxへ入る。
3. 通知画面で `pending/retry/sent/dead` を確認する。dead、または顧客がLINEをブロックしている場合はEmailと電話を確認し、必要なら選択通知を再送待ちに戻す。
4. Taiwan Pay／銀行振込は付款記録を作成し、管理画面の「確認收款」を使う。付款金額は最終合計と一致する必要があり、同一注文にconfirmedを複数作れない。
5. 必要に応じ「出貨準備中」、物流会社・追跡番号を保存して「已出貨」を実行する。未付款注文は出貨できない。
6. 配送完了を確認して「完成」。すべての状態変更は注文監査ログで操作人、時刻、前後状態、変更値を確認できる。

### 取消・退款

未付款で受理／運費確認／等待付款の注文だけ、顧客の署名付きURLまたはAdminから取消できる。二重操作でも在庫は一度しか戻らない。已付款または已出貨後は通常取消を使わず、事業者判断後に退款記録を作る。部分退款は `refunded_amount/refund_status/reason/operator` を残し、全額退款は注文を「退款処理中→退款済み」にする。金流provider側の実退款は契約したproviderの正式手順が別途必要。

### TESTデータ

```bash
python manage.py manage_test_data
python manage.py manage_test_data --action unpublish --execute
python manage.py manage_test_data --action delete-safe --execute
```

最初のコマンドは一覧だけ。`delete-safe` は注文明細に紐づく商品を削除せず、会計履歴を保護する。作品・出版物は物理削除せず非公開化する。

## 通知worker

Webリクエストは外部APIを呼ばず、PostgreSQL Outboxへ記録する。常時実行するworker:

```bash
python manage.py process_notification_outbox --limit 100000 --poll-seconds 2
```

RenderでBackground Workerを追加すると料金が変わる可能性があるため、Blueprintでは自動作成していない。追加後に `OUTBOX_WORKER_CONFIGURED=True` とし、LINE／Emailのtest通知がsentになることを確認する。緊急時はRender Shellで `python manage.py process_notification_outbox --once` を実行できるが、常時運用の代替ではない。

## 障害時

- `/healthz/`: process + DB。外部API障害では503にしない。
- `/readiness/`: DB、migration、Outbox件数。未適用migrationのみ503。
- 5xx／slow request／LINE／Email／Outbox失敗は構造化ログを確認する。
- LINE 409は同じretry keyが既に受理済みのため成功扱い。429、5xx、timeoutは24時間以内の同一retry keyで指数バックオフする。
- 在庫不整合が疑われる場合、商品を一時非公開にし、OrderItemの予約フラグ、取消監査ログ、現在stockを突合する。履歴を削除しない。

## Credential rotation・管理者停止

1. Provider側で新credentialを発行する。
2. Renderの該当環境変数へ登録し、staging疎通を確認する。
3. deploy後、旧credentialを失効する。秘密値をGit・ログ・画面へ貼らない。
4. 退職・役割変更時はDjango Userの `is_active` / `is_staff` を外し、全sessionを失効し、共有credentialをrotateする。
5. Superuserを日常利用せず、商品、注文、問い合わせ等のGroup別権限を付与する。Admin loginにはPostgreSQL共有rate limitがある。TOTPは既存管理者をロックアウトしない導入計画と回復コード運用を確定してから追加する。

## 個人情報請求

Admin「Privacy requests」に閲覧、訂正、処理停止、匿名化の申請と本人確認・対応結果を記録する。法定保存が見込まれる付款／会計記録は無条件削除しない。保存期間経過後の「付款のない取消注文」だけ、dry-run確認後に匿名化できる。

```bash
python manage.py anonymize_expired_customer_data
python manage.py anonymize_expired_customer_data --execute
```
