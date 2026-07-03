import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// Public documentation site for realtime-calib.
// Decisions: ADR-0024 (Docusaurus, docs-site/ in main repo, EN, GitHub Pages, versioned)
// and ADR-0025 (AGPL-3.0 + commercial license + CLA).

const config: Config = {
  title: 'realtime-calib',
  tagline: 'Real-time multi-camera calibration — intrinsics, extrinsics, live feedback',
  favicon: 'img/favicon.png',

  future: {
    v4: true,
  },

  // Production URL and base path for GitHub Pages project site.
  url: 'https://hans-brgs.github.io',
  baseUrl: '/realtime-calib/',

  organizationName: 'hans-brgs',
  projectName: 'realtime-calib',
  deploymentBranch: 'gh-pages',
  trailingSlash: false,

  onBrokenLinks: 'warn',
  onBrokenMarkdownLinks: 'warn',

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
      respectPrefersColorScheme: true,
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
          label: 'GitHub',
          position: 'right',
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
            {label: 'Guides', to: '/docs/guides/configure-cameras'},
            {label: 'Reference', to: '/docs/reference/configuration-format'},
            {label: 'Research', to: '/docs/research/methodology'},
          ],
        },
        {
          title: 'Project',
          items: [
            {label: 'Architecture', to: '/docs/architecture/overview'},
            {label: 'Contributing', to: '/docs/contributing/dev-setup'},
            {label: 'License (AGPL-3.0)', to: '/docs/contributing/license'},
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
              to: '/docs/contributing/license#commercial-use',
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
