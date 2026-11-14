import type { Metadata } from "next";
import { Navbar } from "@/components/home/navbar";
import { Footer } from "@/components/home/footer";
import { DemoBooking } from "./demo-booking";

export const metadata: Metadata = {
  title: "Book a Demo",
  description: "Tell us about your business and choose a time for your 30-minute Talk-Lee demo.",
};

const CALENDAR_URL =
  "https://calendar.google.com/calendar/appointments/schedules/AcZssZ3VEktu7gPGKJcZVfxi45o6GcDdU8RYpSelY9EDJqje_tQn9L6ts1TX0FtnfDH1HyNVeDkBLXM8?gv=true";

export default function BookADemoPage() {
  return (
    <main className="home-navbar-offset bg-cyan-50 dark:bg-black">
      <Navbar />
      <section className="py-12 md:py-16 px-4 md:px-6 lg:px-8">
        <div className="max-w-5xl mx-auto">
          <h1 className="text-3xl md:text-5xl font-bold text-primary dark:text-foreground mb-4">
            Book a Demo
          </h1>
          <p className="text-gray-700 dark:text-muted-foreground mb-8">
            Tell us a little about your business, then choose a time for your 30-minute demo.
          </p>
          <DemoBooking calendarUrl={CALENDAR_URL} />
        </div>
      </section>
      <Footer />
    </main>
  );
}
