import { Navbar } from "@/components/home/navbar";
import { HomeLazySections } from "@/components/home/home-lazy-sections";
import type { Metadata } from "next";
import localFont from "next/font/local";
import { Manrope, Orbitron } from "next/font/google";

const satoshi = localFont({
  src: [
    { path: "../fonts/satoshi/Satoshi-400.woff2", weight: "400", style: "normal" },
    { path: "../fonts/satoshi/Satoshi-500.woff2", weight: "500", style: "normal" },
    { path: "../fonts/satoshi/Satoshi-700.woff2", weight: "700", style: "normal" },
  ],
  display: "swap",
});

const manrope = Manrope({
  subsets: ["latin"],
  variable: "--font-manrope",
});

const orbitron = Orbitron({
  subsets: ["latin"],
  variable: "--font-orbitron",
});

export const metadata: Metadata = {
  title: "Talk-Lee",
  description:
    "Scale conversations instantly with Talkly AI. Smart voice agents deliver support, scheduling, and compliance worldwide.",
};

export default function Home() {
  return (
    <>
      <link rel="preload" as="video" href="/images/ai-voice-section..mp4" />
      <main id="home" className={`home-navbar-offset homepage-bg ${satoshi.className} ${manrope.variable} ${orbitron.variable}`}>
        <Navbar />
        <HomeLazySections />
      </main>
    </>
  );
}
