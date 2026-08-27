import type { Metadata } from "next";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { ContactSection } from "@/components/home/contact-section";

export const metadata: Metadata = {
  title: "Contact Talk-Lee AI",
  description: "Get in touch with our team to learn how Talk-Lee can help.",
};

export default function ContactPage() {
  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <ContactSection sectionClassName="py-12 md:py-16" />
      <Footer />
    </main>
  );
}
