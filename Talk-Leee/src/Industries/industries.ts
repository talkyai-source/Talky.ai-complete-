import { Briefcase, Building2, GraduationCap, HeartPulse, Landmark, Laptop, Luggage, Megaphone, ShoppingCart, UserCheck } from "lucide-react";

export const industryNavItems = [
  {
    label: "Healthcare",
    href: "/industries/healthcare",
    description: "Answer Routine Patient Calls and Collect Requests for Your Team",
    icon: HeartPulse,
  },
  {
    label: "Recruitment",
    href: "/industries/recruitment",
    description: "Call Candidates, Ask Your Screening Questions, and Pass the Answers On",
    icon: UserCheck,
  },
  {
    label: "Marketing Automation",
    href: "/industries/marketing-automation",
    description: "Qualify Leads and Run Outbound Calling Campaigns for Your Clients",
    icon: Megaphone,
  },
  {
    label: "Financial Services",
    href: "/industries/financial-services",
    description: "Answer Routine Enquiries From Approved Information and Collect Requests",
    icon: Landmark,
  },
  {
    label: "Travel Industry",
    href: "/industries/travel-industry",
    description: "Answer Routine Guest and Traveller Calls and Collect Requests for Review",
    icon: Luggage,
  },
  {
    label: "Retail & E-commerce",
    href: "/industries/retail-ecommerce",
    description: "Answer Product and Store Questions by Phone and Collect Customer Requests",
    icon: ShoppingCart,
  },
  {
    label: "Real Estate",
    href: "/industries/real-estate",
    description: "Answer Property Enquiries by Phone and Schedule Callbacks for Your Agents",
    icon: Building2,
  },
  {
    label: "Professional Services",
    href: "/industries/professional-services",
    description: "Answer Routine Client Calls and Collect Enquiries for Your Team",
    icon: Briefcase,
  },
  {
    label: "Software & Tech Support",
    href: "/industries/software-tech-support",
    description: "Answer Routine Support Calls and Collect Requests for Your Team",
    icon: Laptop,
  },
  {
    label: "Education",
    href: "/industries/education",
    description: "Answer Routine School and Student Calls and Collect Requests for Staff",
    icon: GraduationCap,
  },
] as const;
