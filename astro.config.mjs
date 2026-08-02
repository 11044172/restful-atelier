import { defineConfig } from 'astro/config';

const rawBase = process.env.BASE_PATH || '/';
const base = rawBase === '/' ? '/' : `/${rawBase.replace(/^\/+|\/+$/g, '')}`;

export default defineConfig({
  site: process.env.SITE_URL || 'https://example.github.io',
  base,
  output: 'static',
  trailingSlash: 'always',
});
