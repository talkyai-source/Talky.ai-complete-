import type { Metadata, Viewport } from "next";
import "./globals.css";
import { SuspensionStateProvider } from "@/components/admin/suspension-state-provider";
import { AppProviders } from "@/components/providers/app-providers";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;

export const metadata: Metadata = {
  title: "Talk-Lee",
  description: "Intelligent voice communication platform powered by advanced AI agents",
  manifest: "/site.webmanifest",
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
      <body className="font-sans antialiased">
        <AppProviders>
          <SuspensionStateProvider>
            {children}
          </SuspensionStateProvider>
        </AppProviders>
      </body>
    </html>
  );
}
