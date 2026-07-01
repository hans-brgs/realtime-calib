/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_HOST_IP: string;
  readonly VITE_API_URL: string;
  readonly VITE_LIVEKIT_URL: string;
  readonly VITE_TOKEN_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// CSS-only side-effect modules (@fontsource-variable) ship no type declarations.
declare module '@fontsource-variable/sora';
declare module '@fontsource-variable/manrope';
