import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { AuthProvider } from "@/lib/auth-context";
import { NotificationToaster } from "@/components/notifications/notification-toaster";
import { AppProviders } from "@/components/providers/app-providers";

const inter = localFont({
  src: [
    { path: "../fonts/satoshi/Satoshi-400.woff2", weight: "400", style: "normal" },
    { path: "../fonts/satoshi/Satoshi-500.woff2", weight: "500", style: "normal" },
    { path: "../fonts/satoshi/Satoshi-700.woff2", weight: "700", style: "normal" },
  ],
  display: "swap",
  variable: "--font-inter",
});

const manrope = localFont({
  src: [
    { path: "../fonts/satoshi/Satoshi-400.woff2", weight: "400", style: "normal" },
    { path: "../fonts/satoshi/Satoshi-500.woff2", weight: "500", style: "normal" },
    { path: "../fonts/satoshi/Satoshi-700.woff2", weight: "700", style: "normal" },
  ],
  display: "swap",
  variable: "--font-manrope",
});

const orbitron = localFont({
  src: [
    { path: "../fonts/satoshi/Satoshi-400.woff2", weight: "400", style: "normal" },
    { path: "../fonts/satoshi/Satoshi-500.woff2", weight: "500", style: "normal" },
    { path: "../fonts/satoshi/Satoshi-700.woff2", weight: "700", style: "normal" },
  ],
  display: "swap",
  variable: "--font-orbitron",
});

export const metadata: Metadata = {
  title: "Talk-Lee",
  description: "Intelligent voice communication platform powered by advanced AI agents",
  manifest: "/site.webmanifest",
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
    <html lang="en">
      <head>
        <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
      </head>
      <body className={`${inter.variable} ${manrope.variable} ${orbitron.variable} font-sans antialiased`}>
        <AppProviders>
          <AuthProvider>
            {children}
            <NotificationToaster />
          </AuthProvider>
        </AppProviders>
      </body>
    </html>
  );
}
