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
    "Configure inbound and outbound calling with Talk-Lee AI. Manage voice agents, campaign knowledge and call records.",
};

export default function Home() {
  return (
    <>
      {/* Downloads the hero video once, while the HTML is still parsing, and
          hands the same blob URL to both crossfade <video> elements so it does
          not wait for hydration (see NavbarHeroBackgroundVideo). */}
      <script
        dangerouslySetInnerHTML={{
          __html:
            'window.__heroVideo=fetch("/images/hero-navbar-video.mp4").then(function(r){return r.ok?r.blob():null}).catch(function(){return null});' +
            'window.__heroVideo.then(function(b){if(!b)return;var u=URL.createObjectURL(b);window.__heroVideoUrl=u;' +
            'function a(){var v=document.querySelectorAll("video[data-hero-video]");for(var i=0;i<v.length;i++)v[i].src=u}' +
            'if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",a);else a()});',
        }}
      />
      <main id="home" className={`home-navbar-offset homepage-bg ${satoshi.className} ${manrope.variable} ${orbitron.variable}`}>
        <Navbar />
        <HomeLazySections />
      </main>
    </>
  );
}
