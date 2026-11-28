import { AvailabilityLabel } from "@/components/ui/availability-label";
import { PUBLIC_CONNECTORS } from "@/lib/public-connector-status";

// Server component: no state, no browser API. It lists what the dashboard's
// Connectors page has, with the status from lib/public-connector-status.ts.
// It carries no button or link: a connector that is not Available must not
// invite use, and the Available one is connected inside the dashboard.
export function ConnectorStatusSection() {
  return (
    <section id="connectors" className="bg-cyan-50 dark:bg-black py-16 lg:py-20 px-4 md:px-6 lg:px-8">
      <div className="max-w-7xl mx-auto">
        <div className="text-center max-w-3xl mx-auto mb-10 lg:mb-12 space-y-3">
          <h2 className="text-3xl md:text-4xl font-bold tracking-tight text-primary dark:text-foreground">Connectors</h2>
          <p className="text-base sm:text-lg font-light text-gray-700 dark:text-muted-foreground">
            These are the connectors in the dashboard, with the availability of each.
          </p>
        </div>
        <ul className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 lg:gap-6" role="list">
          {PUBLIC_CONNECTORS.map((connector) => (
            <li
              key={connector.name}
              data-connector={connector.name}
              className="rounded-2xl border border-border/70 bg-transparent backdrop-blur-sm p-6"
              style={{
                backgroundImage: "var(--home-card-gradient)",
                backgroundSize: "cover",
                backgroundRepeat: "no-repeat",
              }}
            >
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-lg font-semibold text-primary dark:text-foreground">{connector.name}</h3>
                <AvailabilityLabel value={connector.status} />
              </div>
              <p className="mt-3 text-sm sm:text-base text-gray-700 dark:text-muted-foreground leading-relaxed">{connector.does}</p>
              <p className="mt-2 text-sm text-gray-700 dark:text-muted-foreground leading-relaxed">{connector.customerMust}</p>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
