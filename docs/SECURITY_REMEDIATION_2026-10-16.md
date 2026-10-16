# セキュリティ修正記録（2026-10-16）

対象基準: `7da65208044e2dc28b0ed3d3127ac7a0f61fff51`。修正は作業ツリーに実装し、本番へのデプロイ、実決済、外部バケットの変更、実メール送信は行っていない。

## 実装した対策

| ID | コードの変更 | 残る確認・条件 |
| --- | --- | --- |
| SEC-01 | 返金には `orders.change_payment`、`orders.change_order`、新設 `orders.record_refund` のすべてを要求。アクション本体と選択オブジェクトの権限も検査。Outbox/LINE再送は対応するchange権限が必要。担当者を監査記録へ保存 | migration適用後、返金担当者のGroupだけに専用権限を付与する |
| SEC-02 | 全3経路で非公開stagingから実データを上限付きで取得。形式・実寸・画素数・フレーム数を検証し、PillowでEXIF等を引き継がない新しい画像へ再生成。検証前のキーを公開モデルへ紐付けない | 独立した非公開R2バケットの準備が必要。既存の承認済み画像は一括再検証していない |
| SEC-03 | PUT署名に正確なContentLengthを含める。公開画像は別バケット・別UUIDキーに再生成。全経路共通でユーザーごとに毎時60件/200MiBの発行制限。期限切れ完了を拒否。PostgreSQLのロックで同時発行・完了を制御 | 実R2でのサイズ拒否とブラウザ互換性、非公開設定、lifecycle削除は未検証。完全解消とは判定しない。詳細は下記 |
| SEC-04 | Friendship API障害時に任意の `friendship_status_changed=true` を証拠として使わない。本人認証は維持し、成功したAPIまたは署名検証済みwebhook等の既存証拠がない顧客は購入不可 | 外部APIはmockで検証 |
| SEC-05 | 管理画面の商品出力をCSVから、全セルを明示的な文字列として保存するXLSXへ変更。`import_product_data` はXLSXにも対応。数式を含む入力workbookは拒否し、XML解析にdefusedxmlを使用。従来の機械処理用CSV取込は維持 | Excel/LibreOffice実機での開く・保存・再取込は未検証。openpyxlによる再保存・再取込を確認。32,767文字を超えるセルや非対応制御文字は黙って切り捨てず出力を中止 |
| SEC-06 | 5つの脆弱なNode依存を更新し、CI/定期監査から `--prod` を除去。moderate以上で失敗させる | npm/PyPI監査の結果は実行時点のアドバイザリに限る |
| SEC-07 | 決済結果URLを含む全署名パスを共通ルールでマスク。通常/404/500/slow requestに加え、ログ整形後の例外tracebackもマスク | Render/Cloudflare側のログと過去ログは管理コンソールで確認が必要。Gunicornの現行access形式は元々URLを出力しない |
| SEC-08 | 匿名readinessはstatusだけに限定。通知テーブルへの集計を除去し、migrationグラフは30秒キャッシュ。応答はno-store | 詳細な運用状態は既存の管理画面/CLIで確認 |
| SEC-09 | 顧客用と社内用のメールを別メッセージに分離。管理画面URLは社内用だけに付加 | 既存のOutbox再試行はat-least-once送信。SMTP成功後のプロセス停止等で重複送信を完全には排除しない |

追加でcheckoutと注文完了画面を `no-store` にした。

## 本番反映に必要な設定

1. 通常のデプロイ手順で新しいPython lockをインストールし、`python manage.py migrate --noinput` を実行する。既存の `predeploy.sh` もmigrationを実行する。
2. Django管理のGroup設定で返金担当者に `可登記退款` を付与する。`change_payment` と `change_order` も必要。閲覧担当者へは付与しない。superuserには従来通り全権限がある。
3. 公開画像用とは別のR2バケットを作成し、`ADMIN_IMAGE_STAGING_BUCKET` を設定する。r2.devの公開アクセスとカスタムドメインを両方無効にする。同じS3資格情報が指定した2バケットだけに必要な読み書きをできるよう設定する。
4. stagingバケットで、管理画面の実際のoriginからのPUTとContent-Typeを許可するCORSを設定する。公開用バケットへのブラウザPUTは不要。`pending/` を1日で削除するlifecycleルールを必ず設定する。
5. 管理画面から小さいJPEG/PNG/WebPをアップロードし、完了・表示・保存・削除まで確認する。ブラウザがContent-Lengthを自動生成するため、JavaScriptからこの禁止ヘッダーは設定しない。実R2で、署名時と異なるサイズのPUTが失敗することと、同じ署名URLの再利用で公開画像が変わらないことを隔離バケットで確認する。
6. 放置されたDB追跡行と未紐付けの検証済み画像は既存コマンドで定期清掃する。下記は削除を伴う運用コマンドであり、本作業では本番実行していない。

```sh
python manage.py cleanup_direct_image_uploads --delete
python manage.py cleanup_orphan_product_images --hours 24
python manage.py cleanup_orphan_project_images --hours 24
```

`ADMIN_IMAGE_STAGING_BUCKET` が未設定または公開用と同じ場合、新規画像の署名発行は失敗する。既存画像の表示と編集は継続する。更新前に発行した未完了アップロードは再選択が必要。

[Cloudflareの仕様](https://developers.cloudflare.com/r2/api/s3/presigned-urls/)上、署名URLは失効まで再利用できる。今回の対策で再利用が公開画像を書き換える経路は除去したが、同じURLでの反復PUTの総通信量をDjangoは計測・制限できない。発行数/発行サイズの上限は累積したR2通信量の上限ではない。厳密な1回限りの書込みや反復転送の制限には、別途Worker等の受付口と共有状態による制御が必要。

画像検証は上限を適用した上でWebワーカー内で同期実行する。負荷を観測して別ワーカーへ分離すること、既存公開画像の再検証は追加の運用課題となる。

## 検証

- 既存314件に、拒否と正規動作を確認する27件を追加した。
- 隔離PostgreSQL 14（UTF-8）で341件成功。同時発行と二重完了の2件を含む。CIは従来通りPostgreSQL 17で同じテストを実行する。17自体のローカル実行は未実施。
- 全Node依存358件の監査で既知脆弱性0件。Astro check/build成功（33ページ）。
- Python lockの25パッケージをpip-auditで検査し既知脆弱性0件。
- Djangoのmigration差分検査、CI相当のruff構文検査、ダミーSECRET_KEY・DEBUG=False・HTTPS強制による `check --deploy` が成功。本番環境変数全体の検証ではない。
- 公開サイト、実R2、実メール・LINE送信、実決済を使う検証は行っていない。

```sh
# 通常のテストに登録済み。特にこの修正を確認する場合:
python manage.py test core.tests.test_security_remediation --verbosity 2
# 全体:
python manage.py test --verbosity 2
pnpm audit --audit-level moderate
ASTRO_TELEMETRY_DISABLED=1 pnpm build
pip-audit -r requirements.lock --no-deps --disable-pip
```

元の `docs/security-audit-2026-10-16/reproduce.py` と観測結果は修正前の証拠として保持した。このスクリプトのAuditObservationsは脆弱性があると成功するため、修正後の合否判定には使用しない。修正後の判定には通常テスト内の `core.tests.test_security_remediation` を使用する。

CSP強制、管理者MFA、Turnstile本番設定、Render/CloudflareのWAF・代替ドメイン・プロキシ設定、在庫予約期限、バックアップと過去ログの扱いは、元の報告の追加確認事項として残る。

検証ログ: [PostgreSQL全テスト](security-remediation-2026-10-16/postgresql-tests.txt)、[Node監査](security-remediation-2026-10-16/node-audit.json)、[Python監査](security-remediation-2026-10-16/python-audit.json)、[Astro build](security-remediation-2026-10-16/astro-build.txt)、[Django deploy check](security-remediation-2026-10-16/deploy-check.txt)。
