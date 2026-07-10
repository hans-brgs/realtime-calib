import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// Public documentation site for realtime-calib.
// Decisions: ADR-0024 (Docusaurus, docs-site/ in main repo, EN, GitHub Pages, versioned)
// and ADR-0025 (AGPL-3.0 + commercial license + CLA).

// Served at the custom-domain root (realtime-calib.hans-brgs.dev), so baseUrl is '/'
// in both dev and production.

const SITE_URL = 'https://realtime-calib.hans-brgs.dev';
const REPO_URL = 'https://github.com/hans-brgs/realtime-calib';

// One SEO/GEO description, reused for the page metadata and the structured data.
const SITE_DESCRIPTION =
  'Real-time, open-source multi-camera calibration: recover camera intrinsics ' +
  '(focal length, distortion) and 6-DoF extrinsics for a rig of USB cameras, with ' +
  'live feedback. Runs headless in Docker, driven from any device, with ' +
  'Caliscope-compatible and engine-ready exports (Unity, Unreal, Blender, three.js, ROS).';

// JSON-LD structured data (schema.org), injected site-wide via `headTags`.
// Lets Google understand the project (rich results) and helps AI assistants
// (ChatGPT, Claude, Perplexity) cite it accurately — a GEO lever.
const structuredData = {
  '@context': 'https://schema.org',
  '@graph': [
    {
      '@type': 'SoftwareApplication',
      name: 'realtime-calib',
      applicationCategory: 'DeveloperApplication',
      operatingSystem: 'Linux, Docker',
      description: SITE_DESCRIPTION,
      url: SITE_URL,
      downloadUrl: REPO_URL,
      softwareHelp: `${SITE_URL}/docs/intro`,
      license: 'https://www.gnu.org/licenses/agpl-3.0.html',
      isAccessibleForFree: true,
      offers: {'@type': 'Offer', price: '0', priceCurrency: 'USD'},
      author: {
        '@type': 'Person',
        name: 'Hans Bourgeois',
        url: 'https://github.com/hans-brgs',
        affiliation: {'@type': 'Organization', name: 'Myosin'},
      },
    },
    {
      '@type': 'WebSite',
      name: 'realtime-calib',
      url: SITE_URL,
      description: SITE_DESCRIPTION,
      inLanguage: 'en',
      potentialAction: {
        '@type': 'SearchAction',
        target: `${SITE_URL}/search?q={search_term_string}`,
        'query-input': 'required name=search_term_string',
      },
    },
    {
      '@type': 'Person',
      name: 'Hans Bourgeois',
      url: 'https://github.com/hans-brgs',
      affiliation: {'@type': 'Organization', name: 'Myosin'},
    },
  ],
};

const config: Config = {
  title: 'realtime-calib',
  tagline: 'Real-time multi-camera calibration — intrinsics, extrinsics, live feedback',
  favicon: 'img/favicon.png',

  // Production URL and base path (custom domain on GitHub Pages).
  url: SITE_URL,
  baseUrl: '/',

  organizationName: 'hans-brgs',
  projectName: 'realtime-calib',
  deploymentBranch: 'gh-pages',
  trailingSlash: false,

  onBrokenLinks: 'throw',

  // Inject the site-wide JSON-LD structured data into <head>.
  headTags: [
    {
      tagName: 'script',
      attributes: {type: 'application/ld+json'},
      innerHTML: JSON.stringify(structuredData),
    },
  ],

  markdown: {
    mermaid: true,
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },

  themes: ['@docusaurus/theme-mermaid'],

  // English only for now; i18n scaffolding kept so locales can be added later.
  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl:
            'https://github.com/hans-brgs/realtime-calib/tree/main/docs-site/',
          // Versioning is enabled via `yarn docusaurus docs:version <x.y>`.
        },
        blog: {
          showReadingTime: true,
          blogTitle: 'Releases & notes',
          blogDescription: 'Release announcements and project notes',
          feedOptions: {
            type: ['rss', 'atom'],
            xslt: true,
          },
          editUrl:
            'https://github.com/hans-brgs/realtime-calib/tree/main/docs-site/',
          onInlineTags: 'warn',
          onInlineAuthors: 'warn',
          onUntruncatedBlogPosts: 'warn',
        },
        theme: {
          customCss: './src/css/custom.css',
        },
        // Search-engine sitemap. Drop thin/no-SEO-value routes:
        // blog taxonomies (tags/authors/archive) and the search page.
        sitemap: {
          lastmod: 'date',
          changefreq: 'weekly',
          priority: 0.5,
          ignorePatterns: [
            '/search',
            '/blog/archive',
            '/blog/authors',
            '/blog/authors/**',
            '/blog/tags',
            '/blog/tags/**',
          ],
          filename: 'sitemap.xml',
        },
      } satisfies Preset.Options,
    ],
  ],

  // Auto-generate llms.txt + llms-full.txt + per-page Markdown for AI engines,
  // regenerated on every build so it never drifts from the docs.
  plugins: [
    [
      '@signalwire/docusaurus-plugin-llms-txt',
      {
        siteTitle: 'realtime-calib',
        siteDescription: SITE_DESCRIPTION,
        depth: 2,
        content: {
          enableLlmsFullTxt: true,
          excludeRoutes: [
            '/search',
            '/blog/archive',
            '/blog/authors/**',
            '/blog/tags/**',
            '/docs/category/**',
          ],
        },
      },
    ],
  ],

  themeConfig: {
    image: 'img/social-card.png',
    // Global SEO meta tags (long-tail target queries + author).
    metadata: [
      {
        name: 'keywords',
        content:
          'camera calibration, multi-camera calibration, real-time camera calibration, ' +
          'camera intrinsics, camera extrinsics, ChArUco, ArUco, USB camera calibration, ' +
          'headless calibration, Caliscope alternative, bundle adjustment, reprojection error, ' +
          'motion capture calibration, stereo calibration, OpenCV calibration',
      },
      {name: 'author', content: 'Hans Bourgeois (Myosin)'},
      // Google Search Console: once you have the token, add
      // {name: 'google-site-verification', content: '<token>'},
    ],
    // Algolia DocSearch. The apiKey is the public "search-only" key (safe to commit).
    algolia: {
      appId: 'HIS77Z2B0I',
      apiKey: 'cdbe99e22be2a3fbb2cbd0a70cd4c6bf',
      indexName: 'realtime-calib-documentation-crawler',
      contextualSearch: true,
    },
    colorMode: {
      defaultMode: 'dark',
      disableSwitch: true,
      respectPrefersColorScheme: false,
    },
    navbar: {
      title: 'realtime-calib',
      logo: {
        alt: 'realtime-calib logo',
        src: 'img/logo.png',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'docsSidebar',
          position: 'left',
          label: 'Docs',
        },
        {to: '/docs/faq', label: 'FAQ', position: 'left'},
        {to: '/docs/research/methodology', label: 'Research', position: 'left'},
        {to: '/blog', label: 'Releases', position: 'left'},
        {
          href: 'https://github.com/hans-brgs/realtime-calib',
          position: 'right',
          className: 'header-github-link',
          'aria-label': 'GitHub repository',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {label: 'Getting Started', to: '/docs/intro'},
            {label: 'Guides', to: '/docs/guides/start-or-load-session'},
            {label: 'Reference', to: '/docs/reference/output-calibration-files'},
            {label: 'Research', to: '/docs/research/methodology'},
            {label: 'FAQ', to: '/docs/faq'},
          ],
        },
        {
          title: 'Project',
          items: [
            {label: 'Architecture', to: '/docs/architecture/overview'},
            {label: 'License (AGPL-3.0)', to: '/docs/open-source/license'},
            {label: 'GitHub', href: 'https://github.com/hans-brgs/realtime-calib'},
          ],
        },
        {
          title: 'Support',
          items: [
            {
              label: 'Sponsor on GitHub',
              href: 'https://github.com/sponsors/hans-brgs',
            },
            {
              label: 'Buy me a token ☕',
              href: 'https://ko-fi.com/myosin',
            },
            {
              label: 'Commercial license',
              to: '/docs/open-source/license#commercial-use',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} Hans Bourgeois (Myosin). realtime-calib is licensed under AGPL-3.0.`,
    },
    prism: {
      theme: prismThemes.oneLight,
      darkTheme: prismThemes.oneDark,
      additionalLanguages: ['bash', 'python', 'toml', 'json', 'docker'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
