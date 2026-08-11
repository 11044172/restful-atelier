# Security Operations

- CSPは既存のinline JSON-LD／styleを破壊しないようReport-Onlyから開始している。ブラウザレポートを収集し、nonceまたは外部化を完了してからenforceへ切り替える。`unsafe-inline`やワイルドカードは追加しない。
- `TRUSTED_PROXY_IPS`にRenderで実際に確認した直前proxyだけを登録する。未設定時は `REMOTE_ADDR` のみ使い、X-Forwarded-Forを信頼しない。
- Adminは強い個別account、最小権限Group、共有account禁止。Login rate limitはPostgreSQL共有。TOTP導入時は2名以上の回復管理者、回復コード、段階適用、緊急解除手順を先に用意する。
- LINE、SMTP、R2、Turnstile、DB、Django SECRET_KEYのrotation日と担当者を記録する。LINE retry keyは24時間を超えて自動再利用しない。
- 依存関係は `requirements.lock` と `pnpm-lock.yaml` をrelease sourceとし、CIのpip-audit、pnpm audit、gitleaksを全て確認する。
- 5xx、付款失敗、LINE/Email dead、Outbox backlog、slow request、R2失敗、在庫不整合をalert対象にする。Sentry等は未契約のため必須化していない。
- インシデント時はログと監査記録を保全し、秘密値をrotateし、影響範囲・個人情報・通知義務を事業者と専門家が評価する。
