import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// Public documentation site for realtime-calib.
// Decisions: ADR-0024 (Docusaurus, docs-site/ in main repo, EN, GitHub Pages, versioned)
// and ADR-0025 (AGPL-3.0 + commercial license + CLA).

// Served at the custom-domain root (realtime-calib.hans-brgs.dev), so baseUrl is '/'
// in both dev and production.

const config: Config = {
  title: 'realtime-calib',
  tagline: 'Real-time multi-camera calibration — intrinsics, extrinsics, live feedback',
  favicon: 'img/favicon.png',

  // Production URL and base path (custom domain on GitHub Pages).
  url: 'https://realtime-calib.hans-brgs.dev',
  baseUrl: '/',

  organizationName: 'hans-brgs',
  projectName: 'realtime-calib',
  deploymentBranch: 'gh-pages',
  trailingSlash: false,

  onBrokenLinks: 'warn',

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
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    image: 'img/social-card.png',
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
        {to: '/docs/research/methodology', label: 'Research', position: 'left'},
        {to: '/blog', label: 'Releases', position: 'left'},
        {
          type: 'docsVersionDropdown',
          position: 'right',
        },
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
