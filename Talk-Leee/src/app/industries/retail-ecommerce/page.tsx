import type { Metadata } from "next";
import Link from "next/link";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import { Video } from "lucide-react";
import Image from "next/image";

export const metadata: Metadata = {
  title: "AI for Retail & E-commerce",
  description: "Smarter Conversations. Faster Sales. Better Customer Experience.",
};

export default function RetailEcommerceIndustryPage() {
  const accentCardClassName =
    "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-border hover:shadow-md";
  const accentCardStyle = {
    backgroundImage: "var(--home-card-gradient)",
    backgroundSize: "cover",
    backgroundRepeat: "no-repeat",
  } as const;

  const keyCapabilities = [
    {
      title: "AI Order Inquiry Automation",
      description:
        "Customers frequently call to check order status, delivery timelines, or returns. Talky AI automates these conversations using AI order inquiry automation, providing accurate updates without human intervention.",
    },
    {
      title: "AI Product Inquiry Handling",
      description:
        "From pricing to availability and features, Talky AI manages AI product inquiry handling with speed and accuracy, helping customers make faster buying decisions.",
    },
    {
      title: "AI Voice Agents for Retail",
      description:
        "Our AI voice agents for retail handle inbound calls naturally, understand customer intent, and route conversations intelligently when human support is needed.",
      strongTitle: true,
    },
    {
      title: "AI Inbound Retail Calls Management",
      description:
        "Talky AI efficiently handles AI inbound retail calls, reducing missed calls and improving first-call resolution during busy hours.",
      strongTitle: true,
    },
  ] as const;

  const featuresThatConnect = [
    {
      title: "Advanced Calling Tools",
      description:
        "Handle high call volumes with ease using AI voice agents for retail. Automate common questions, route calls intelligently, and empower your team to focus on complex customer needs.",
    },
    {
      title: "Call Routing and IVR",
      description:
        "Set up smart call routing rules and IVR systems to ensure every customer reaches the right team the first time. With AI inbound retail calls, reduce wait times and improve first-call resolution.",
    },
    {
      title: "E-Commerce Integrations",
      description:
        "Connect Talky AI to your favorite e-commerce platforms to get a 360-degree view of customer activity. Sync orders, product details, and customer history to provide faster, more accurate responses.",
      strongTitle: true,
    },
    {
      title: "Advanced Analytics",
      description:
        "Track every conversation, measure KPIs, and gain actionable insights to improve AI customer engagement retail. Use analytics to identify trends, monitor team performance, and deliver five-star customer experiences.",
      strongTitle: true,
    },
  ] as const;

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight text-primary dark:text-foreground">
            AI for Retail &amp; E-commerce
          </h1>
          <h2 className="mt-6 text-xl md:text-2xl font-semibold text-primary dark:text-foreground">
            Smarter Conversations. Faster Sales. Better Customer Experience.
          </h2>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Retail and e-commerce customers expect instant answers whether they are calling about an order, a product, or availability. Talky AI
            delivers intelligent automation through AI for retail &amp; e-commerce, helping retail brands handle high call volumes, reduce wait
            times, and improve customer engagement across every channel.
          </p>
          <div className="mt-14 text-center">
            <p className="text-xl md:text-2xl font-semibold text-primary dark:text-foreground">
              Ready to revolutionize your customer support?
            </p>
            <div className="mt-8 flex justify-center">
              <Button
                asChild
                size="lg"
                className="rounded-xl px-10 bg-indigo-600 text-white hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-400"
              >
                <Link href="/#contact" className="inline-flex items-center gap-2">
                  <Video className="h-5 w-5" aria-hidden />
                  Request a Free Demo Today
                </Link>
              </Button>
            </div>
          </div>
        </header>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Why Talky AI for Retail &amp; E-commerce Businesses?
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talky AI is built to manage real retail challenges— from peak shopping seasons to daily order inquiries— so no customer call goes
            unanswered. Using AI retail customer support, Talky AI helps businesses respond faster, stay available 24/7, and maintain a consistent
            brand voice across all interactions.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Key Capabilities Designed for Retail Operations
          </h2>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {keyCapabilities.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className="text-lg md:text-xl font-semibold text-primary dark:text-foreground">
                  {"strongTitle" in item && item.strongTitle ? <strong>{item.title}</strong> : item.title}
                </h3>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">{item.description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            AI-Powered Retail Workflow Automation
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Retail teams deal with repetitive tasks daily. AI retail workflow automation helps streamline internal processes by:
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Routing calls to the right department</li>
            <li>• Logging conversations automatically</li>
            <li>• Triggering follow-ups and callbacks</li>
            <li>• Syncing customer data across systems</li>
          </ul>
          <p className="mt-6 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            This reduces manual work and improves operational efficiency.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            AI Retail Call Center That Scales With You
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Whether you manage a small online store or a large retail chain, Talky AI functions as a modern AI retail call center that scales
            effortlessly during sales, promotions, and seasonal spikes.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Features That Connect You With Shoppers
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Deliver seamless shopping experiences and personalized support with <strong>Talky AI</strong>.
          </p>
          <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4">
            {featuresThatConnect.map((item) => (
              <div key={item.title} className={accentCardClassName} style={accentCardStyle}>
                <h3 className="text-lg md:text-xl font-semibold text-primary dark:text-foreground">
                  {"strongTitle" in item && item.strongTitle ? <strong>{item.title}</strong> : item.title}
                </h3>
                <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">{item.description}</p>
              </div>
            ))}
          </div>
          <div className="mt-10 flex justify-center">
            <div className="group w-full max-w-5xl overflow-hidden rounded-3xl border border-border/70 shadow-sm transition-[transform,box-shadow,filter] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:brightness-[1.02]">
              <div className="relative aspect-[1024/576] w-full">
                <Image
                  src="/images/industries/retail-ecommerce/features.png"
                  alt=""
                  fill
                  sizes="(max-width: 768px) 100vw, (max-width: 1024px) 900px, 1024px"
                  quality={100}
                  className="object-cover transition-transform duration-300 ease-out group-hover:scale-[1.02]"
                />
              </div>
            </div>
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Put Customers at the Heart of Every Conversation
          </h2>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Communicate Across Channels</li>
            <li>• Always Know What to Do Next</li>
            <li>• Collaborate Seamlessly</li>
            <li>• Personalize Conversations</li>
            <li>• Threaded Conversations</li>
          </ul>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Conversational Analytics for Retail &amp; E-commerce
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talky AI’s conversational analytics reduce pre- and post-call admin, freeing your team to focus on high-value tasks. Gain insights into
            call trends, customer behavior, and team performance.
          </p>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Why Retailers Choose Talky AI
          </h2>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• AI retail customer support that scales with demand</li>
            <li>• Faster order and product inquiry handling</li>
            <li>• Unified omnichannel communication</li>
            <li>• Personalized customer interactions</li>
            <li>• Reduced support costs and missed calls</li>
          </ul>
        </section>

        <section className="mt-14 flex justify-center">
          <Button
            asChild
            size="lg"
            className="rounded-xl px-10 bg-indigo-600 text-white hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-400"
          >
            <Link href="/#contact">Get Started Today</Link>
          </Button>
        </section>
      </div>
      <Footer />
    </main>
  );
}
