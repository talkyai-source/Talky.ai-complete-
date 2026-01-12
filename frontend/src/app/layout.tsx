import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/lib/auth-context";
import { FloatingAssistantWrapper } from "@/components/ui/floating-assistant-wrapper";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

export const metadata: Metadata = {
  title: "Talky.ai - AI Voice Dialer",
  description: "Intelligent voice communication platform powered by advanced AI agents",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${inter.variable} font-sans antialiased`}>
        <AuthProvider>
          {children}
          <FloatingAssistantWrapper />
        </AuthProvider>
      </body>
    </html>
  );
}

