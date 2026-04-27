import type { Metadata } from "next";
import Link from "next/link";
import Image from "next/image";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { Button } from "@/components/ui/button";
import { Video } from "lucide-react";

export const metadata: Metadata = {
  title: "Smarter Property Management with AI for Real Estate",
  description: "Handle every property inquiry, schedule viewings, and engage leads with AI. Save time and improve client satisfaction effortlessly.",
};

export default function RealEstateIndustryPage() {
  const accentCardClassName =
    "group rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6 shadow-sm transition-[transform,filter,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:brightness-[1.02] hover:border-border hover:shadow-md";
  const accentCardStyle = {
    backgroundImage: "var(--home-card-gradient)",
    backgroundSize: "cover",
    backgroundRepeat: "no-repeat",
  } as const;

  return (
    <main className="home-navbar-offset bg-cyan-100 dark:bg-background">
      <Navbar />
      <div className="mx-auto w-full max-w-6xl px-4 md:px-6 lg:px-8 py-16 md:py-20">
        <header className="text-center">
          <h1 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight leading-[1.06] text-primary dark:text-foreground">
            <span className="block">Smarter Property Management</span>
            <span className="block">with AI for Real Estate</span>
          </h1>
          <h2 className="mt-6 text-xl md:text-2xl font-semibold text-primary dark:text-foreground">
            Automate Communication &amp; Close Deals Faster
          </h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed max-w-4xl mx-auto">
            Manage calls, property inquiries, and appointments seamlessly with AI for real estate.
          </p>
        </header>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Capture Every Lead Instantly</h2>
          <h3 className="mt-6 text-xl md:text-2xl font-semibold text-primary dark:text-foreground">Respond to Prospects in Real-Time</h3>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            In real estate, every minute counts. Talkly AI ensures that no lead goes unanswered by automatically managing incoming inquiries,
            calls, and follow-ups. Whether a prospect wants pricing details, availability, or to schedule a viewing, Talkly AI responds instantly,
            so your team can focus on closing deals.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <h4 className="text-lg md:text-xl font-semibold text-primary dark:text-foreground">Key Features:</h4>
            <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• <strong className="text-primary dark:text-foreground">AI property inquiry handling</strong> - Deliver instant responses to buyers and renters</li>
              <li>• <strong className="text-primary dark:text-foreground">AI real estate call automation</strong> - Handle inbound and outbound calls automatically</li>
              <li>• <strong className="text-primary dark:text-foreground">AI follow-up calls for property leads</strong> - Keep leads engaged 24/7</li>
            </ul>
          </div>
          <div className="mt-10 flex justify-start">
            <div className="group w-full max-w-4xl overflow-hidden rounded-3xl border border-border/70 shadow-sm transition-[transform,box-shadow,filter] duration-200 ease-out hover:-translate-y-0.5 hover:shadow-md hover:brightness-[1.02]">
              <Image
                src="/images/industries/real-estate/real-estate-7.jpg"
                alt="AI interface for real estate inquiries and follow-ups"
                width={1344}
                height={768}
                quality={100}
                className="h-auto w-full transition-transform duration-300 ease-out group-hover:scale-[1.02]"
                sizes="(min-width: 1024px) 896px, (min-width: 768px) 672px, 100vw"
              />
            </div>
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Qualify Leads Smarter</h2>
          <h3 className="mt-6 text-xl md:text-2xl font-semibold text-primary dark:text-foreground">Focus on High-Potential Clients</h3>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Not every inquiry is ready to convert. lead qualification AI for real estate automatically evaluates prospects, ranks them by interest
            and engagement, and highlights the most promising leads. This allows your agents to focus on the opportunities most likely to close,
            improving conversion rates and saving valuable time.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <h4 className="text-lg md:text-xl font-semibold text-primary dark:text-foreground">Key Features:</h4>
            <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Automated lead scoring and ranking</li>
              <li>• Actionable insights for targeted follow-ups</li>
              <li>• Higher conversion rates with less effort</li>
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Effortless Scheduling</h2>
          <h3 className="mt-6 text-xl md:text-2xl font-semibold text-primary dark:text-foreground">Hassle-Free Appointments for Realtors</h3>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Coordinating property viewings and client meetings is often time-consuming. AI appointment scheduling for realtors automatically manages
            calendars, confirms appointments, and sends reminders to clients, reducing missed meetings and improving workflow efficiency.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <h4 className="text-lg md:text-xl font-semibold text-primary dark:text-foreground">Key Features:</h4>
            <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Reduce scheduling conflicts</li>
              <li>• Seamless coordination between agents and clients</li>
              <li>• Professional streamlined client experience</li>
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">24/7 AI Voice Support</h2>
          <h3 className="mt-6 text-xl md:text-2xl font-semibold text-primary dark:text-foreground">
            Virtual Assistants to Handle Every Call
          </h3>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            AI voice agents for real estate act as your virtual assistants, answering routine questions, routing calls to the right team member,
            and providing instant updates—even outside office hours. This ensures prospects always get the attention they need and enhances overall
            client satisfaction.
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <h4 className="text-lg md:text-xl font-semibold text-primary dark:text-foreground">Key Features:</h4>
            <ul className="mt-4 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• Round-the-clock support</li>
              <li>• Personalized, professional communication</li>
              <li>• Reduces workload on your team</li>
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Streamline Your Entire Workflow</h2>
          <h3 className="mt-6 text-xl md:text-2xl font-semibold text-primary dark:text-foreground">
            Advanced Tools to Save Time and Boost Efficiency
          </h3>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Talky AI is more than just a call management system. It’s a full suite of tools designed for AI for real estate teams:
          </p>
          <div className={`mt-8 ${accentCardClassName}`} style={accentCardStyle}>
            <ul className="space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
              <li>• AI virtual receptionist for realtors</li>
              <li>• AI property management automation</li>
              <li>• AI real estate workflow automation</li>
              <li>• AI property booking calls</li>
              <li>• AI real estate customer support</li>
            </ul>
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">
            Deliver Exceptional Client Experiences
          </h2>
          <h3 className="mt-6 text-xl md:text-2xl font-semibold text-primary dark:text-foreground">
            Build Trust and Satisfaction with Every Interaction
          </h3>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Client expectations are higher than ever. With Talkly AI, you can provide fast, professional, and personalized service at every
            touchpoint. By automating routine tasks, your team can focus on meaningful client interactions, building trust, and strengthening
            long-term relationships.
          </p>
          <ul className="mt-6 space-y-2 text-sm sm:text-base text-gray-700 dark:text-muted-foreground">
            <li>• Consistent and professional communication</li>
            <li>• Faster response times for all property inquiries</li>
            <li>• Increased client satisfaction and loyalty</li>
          </ul>
        </section>

        <section className="mt-14 rounded-3xl border border-border/70 bg-background/70 dark:bg-white/5 backdrop-blur-sm p-8 md:p-12">
          <h2 className="text-2xl md:text-3xl font-semibold text-primary dark:text-foreground">Get Started Today</h2>
          <p className="mt-4 text-sm sm:text-base md:text-lg text-gray-700 dark:text-muted-foreground leading-relaxed">
            Stop juggling calls, inquiries, and appointments manually.
          </p>
          <div className="mt-8 flex justify-center">
            <Button
              asChild
              size="lg"
              className="h-14 px-12 text-base sm:text-lg bg-blue-600 text-white hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-400 [&_svg]:size-5"
            >
              <Link href="/#contact" className="inline-flex items-center gap-2">
                <Video aria-hidden />
                Request a Demo
              </Link>
            </Button>
          </div>
        </section>
      </div>
      <Footer />
    </main>
  );
}
