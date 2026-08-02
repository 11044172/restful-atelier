# 靜院居家 RESTFUL ATELIER

室內設計、生活器物、出版品をひとつの世界観で見せる、クライアント確認用のAstro静的デモです。表示言語は台湾向け繁体字中国語で、全ページに `noindex,nofollow` を設定しています。

## ローカル起動

Node.js 20以上を使用します。

```bash
npm install
npm run dev
```

公開用の型チェックと静的ビルド、プレビューは次のコマンドです。

```bash
npm run build
npm run preview
```

## データと動的ページ

- 商品：`src/data/products.ts`
- 商品カテゴリ：`src/data/categories.ts`
- 施工事例：`src/data/projects.ts`
- 出版物：`src/data/publications.ts`
- ブランド文章、住所、季節表記：`src/data/site.ts`
- ナビゲーション：`src/data/navigation.ts`

商品を追加するときは `products` 配列へ `Product` 型に沿ったデータを追加します。`slug` はURLになるため、重複しない英小文字のkebab-case（例：`morning-cup`）を指定してください。価格は表示文字列ではなく `price: 1280` のような数値で保存します。`featuredOrder` は精選順、`publishedAt` は `YYYY-MM-DD` 形式の最新順に使用します。`categorySlug` はカテゴリデータに存在するslug、`subcategory` はそのカテゴリの絞り込み項目と一致させます。

`src/pages/shop/products/[slug].astro` が `getStaticPaths` で商品詳細を、`src/pages/shop/category/[slug].astro` が6カテゴリを静的生成します。カテゴリを追加するときは `CategorySlug` 型、`categories` 配列、対応商品を同時に更新してください。

施工事例は `projects` 配列へ一意の `slug` と事例情報、素材、ギャラリー情報を追加します。`src/pages/works/[slug].astro` が `getStaticPaths` で各詳細ページを静的生成します。`projectType` は一覧の絞り込み項目と一致させてください。

季節表示は `src/data/site.ts` の `site.collection` だけを変更します。年、季節、コレクション名、トップ／ショップの小見出し、告知文を一元管理しています。

## 收藏・購物車

ブラウザのlocalStorageを次の形式で使用します。商品名や価格は保存せず、画面表示時にslugを商品データと照合します。存在しないslugや壊れた値は無視されます。

`restful-favorites`：

```json
["morning-cup", "ceramic-vase"]
```

`restful-cart`：

```json
[
  { "slug": "morning-cup", "quantity": 2 },
  { "slug": "linen-tablecloth", "quantity": 1 }
]
```

数量は1以上です。同じ商品を再追加した場合は新しい行を作らず、既存数量を加算します。ヘッダー件数はページ表示、同一ページ内の変更、別タブの `storage` イベント、ブラウザの戻る／進むに同期します。

## GitHub Pages

1. GitHubリポジトリの `Settings > Pages` でSourceを `GitHub Actions` に設定します。
2. `main` ブランチへpushすると `.github/workflows/deploy.yml` が依存関係をインストールし、型チェックと静的ビルド後に公開します。
3. Actionsの `configure-pages` が公開originと `/restful-atelier` のbase pathを検出し、`SITE_URL` / `BASE_PATH` としてAstroへ渡します。

`astro.config.mjs` は `output: 'static'` と `trailingSlash: 'always'` を使用しています。ローカルではbaseが `/`、GitHub Pagesでは環境変数のbaseになるため、内部リンクは `withBase()` に統一してください。

## 写真の差し替え

現在の画像領域は `EditorialImage.astro` が作るCSSベースの仮ビジュアルです。実写真受領後は同コンポーネントに画像パスを受け取るプロパティを追加するか、各呼び出しをAstroのImageコンポーネントへ置き換えてください。既存の `aspect-ratio` は維持するとレイアウトずれを防げます。

## 現段階で未実装の本番機能

このサイトはデザイン、構成、操作感を確認する静的デモです。收藏、購物車、数量変更、絞り込み、並び替え、簡易検索はブラウザ内で動作しますが、以下は未実装です。

- 決済
- 本番在庫管理
- 会員登録
- 注文履歴／注文管理
- 管理画面
- メール送信
- 配送計算
- クーポン
- 正式な商品検索
- CMS連携

問い合わせ送信と決済ボタンはページ遷移や外部送信を行わず、展示用通知だけを表示します。
