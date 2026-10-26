import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { SuspensionStateProvider } from "@/components/admin/suspension-state-provider";
import { AppProviders } from "@/components/providers/app-providers";
import { CallEndedNotice } from "@/components/ui/call-ended-notice";

// Manrope is the navbar's typeface (navbar.tsx sets `font-family: var(--font-manrope)`
// inline) and the navbar renders on every public page, so the variable has to be
// in scope for the whole document, not only the homepage's <main id="home">.
// `.variable` defines the custom property only; body's own font-family is unchanged.
const manrope = localFont({
  src: "../fonts/manrope/Manrope-Variable.ttf",
  weight: "200 800",
  style: "normal",
  display: "swap",
  variable: "--font-manrope",
});

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;

export const metadata: Metadata = {
  title: "Talk-Lee",
  description: "Intelligent voice communication platform powered by advanced AI agents",
  manifest: "/site.webmanifest",
  // Google Search Console ownership proof for https://talkleeai.com/.
  // Must stay in place permanently - removing it revokes verification.
  verification: {
    google: "J9pxYqrFOcxJsYsdazFfu5AAiqE4T-Y66OPLLlaBC9c",
  },
  icons: {
    icon: [{ url: "/favicon.svg", type: "image/svg+xml" }],
  },
};

export const viewport: Viewport = {
  themeColor: "#000000",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `try { document.documentElement.classList.add(localStorage.getItem('talklee.theme') || 'light') } catch(e) {}`,
          }}
        />
        {apiBaseUrl && <link rel="preconnect" href={apiBaseUrl} />}
        {apiBaseUrl && <link rel="dns-prefetch" href={apiBaseUrl} />}
      </head>
      <body className={`font-sans antialiased ${manrope.variable}`}>
        <AppProviders>
          <SuspensionStateProvider>
            {children}
          </SuspensionStateProvider>
        </AppProviders>
        {/* Renders null unless the Ask AI popup flagged a call as dropped by
            navigation — see ASKAI_CALL_ENDED_FLAG in call-ended-notice.tsx. */}
        <CallEndedNotice />
      </body>
    </html>
  );
}
