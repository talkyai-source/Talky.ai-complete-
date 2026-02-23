import { LoadingState } from "@/components/states/page-states";

export default function Loading() {
    return (
        <div className="mx-auto w-full max-w-5xl px-4 py-10">
            <LoadingState title="Loading actions" description="Fetching assistant actions and run history." />
        </div>
    );
}
