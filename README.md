# 靜院居家 RESTFUL ATELIER

室內設計、生活器物、出版品をひとつの世界観で見せる、クライアント確認用の静的Webプロトタイプです。表示言語は台湾向け繁体字中国語です。

## ローカル起動

Node.js 20以上を使用します。

```bash
npm install
npm run dev
```

表示されたローカルURLをブラウザで開いてください。公開用ビルドと確認は次のコマンドです。

```bash
npm run build
npm run preview
```

## GitHub Pagesで公開する

1. GitHubリポジトリの `Settings > Pages` を開き、Sourceを `GitHub Actions` に設定します。
2. `main` ブランチへpushすると、`.github/workflows/deploy.yml` が自動的にビルド・公開します。
3. Project PagesのサブディレクトリはActionsが自動検出し、`BASE_PATH` としてAstroへ渡します。

独自ドメインや別の公開先を使う場合は、`astro.config.mjs` の `site` / `base` の既定値、またはビルド時の `SITE_URL` / `BASE_PATH` 環境変数を変更してください。

## 内容の変更

- ブランド文章・住所：`src/data/site.ts`
- ナビゲーション：`src/data/navigation.ts`
- 商品名・価格・分類：`src/data/products.ts`
- 施工事例：`src/data/projects.ts`
- 出版物：`src/data/publications.ts`
- 商品カテゴリ：`src/data/categories.ts`

ページ固有の長文は各 `src/pages/` ファイルにあります。共通の色・文字・余白は `src/styles/global.css` に集約しています。

## 写真の差し替え

現在の写真領域は `EditorialImage.astro` が作るCSSベースの仮ビジュアルです。実写真受領後は、同コンポーネントに画像パスを受け取るプロパティを追加するか、各呼び出しをAstroのImageコンポーネントへ置き換えてください。既存の `aspect-ratio` は維持するとレイアウトずれを防げます。

## 現段階について

これはデザイン・構成・操作感を確認するための静的デモです。検索、收藏、購物車、数量変更、絞り込み、フォームはブラウザ内で見た目と操作のみを再現しています。フォーム送信、決済、注文、在庫、会員登録、認証、配送連携、CMS、実検索、メール配信は未実装です。本番時には各サービスの選定、API接続、セキュリティ・個人情報・法令対応が必要です。

検索エンジンへの登録を避けるため、全ページに `noindex,nofollow` を設定し、`public/robots.txt` でもクロールを抑制しています。
