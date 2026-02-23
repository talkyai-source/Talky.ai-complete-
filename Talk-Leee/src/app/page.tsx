import { Navbar } from "@/components/home/navbar";
import { HomeLazySections } from "@/components/home/home-lazy-sections";
import localFont from "next/font/local";

const satoshi = localFont({
  src: [
    { path: "../fonts/satoshi/Satoshi-400.woff2", weight: "400", style: "normal" },
    { path: "../fonts/satoshi/Satoshi-500.woff2", weight: "500", style: "normal" },
    { path: "../fonts/satoshi/Satoshi-700.woff2", weight: "700", style: "normal" },
  ],
  display: "swap",
});

export default function Home() {
  return (
    <main id="home" className={`home-navbar-offset homepage-bg ${satoshi.className}`}>
      <Navbar />
      <HomeLazySections />
    </main>
  );
}
