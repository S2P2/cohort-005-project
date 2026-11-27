import { Link } from "react-router";
import { Button } from "~/components/ui/button";
import {
  Tabs,
  TabsContent,
  TabsContentNoShift,
  TabsList,
  TabsTrigger,
} from "~/components/ui/tabs";
import { formatPrice } from "~/lib/utils";

type EnrollButtonProps = {
  course: { slug: string; price: number };
  /** What this Person pays, after any Purchasing-Power discount. */
  pppPrice: number;
  tierLabel: string;
  currentUserId: number | null;
};

function purchasePath(slug: string, team: boolean) {
  return `/courses/${slug}/purchase${team ? "?mode=team" : ""}`;
}

/** Someone signed out is sent to sign up first and comes back to the purchase. */
function purchaseLink(
  slug: string,
  currentUserId: number | null,
  team: boolean
) {
  const path = purchasePath(slug, team);
  return currentUserId
    ? path
    : `/signup?redirectTo=${encodeURIComponent(path)}`;
}

function PurchaseTabsList() {
  return (
    <TabsList className="mb-4 w-full">
      <TabsTrigger value="self" className="flex-1 text-xs">
        Buy for Myself
      </TabsTrigger>
      <TabsTrigger value="team" className="flex-1 text-xs">
        Buy for Team
      </TabsTrigger>
    </TabsList>
  );
}

/** The price, with the full price struck through when it is discounted. */
function PriceDisplay({
  fullPrice,
  priceLabel,
  tierLabel,
  discounted,
}: {
  fullPrice: number;
  priceLabel: string;
  tierLabel: string;
  discounted: boolean;
}) {
  return (
    <div className="mb-3 text-center">
      {discounted ? (
        <>
          <div className="text-sm text-muted-foreground line-through">
            {formatPrice(fullPrice)}
          </div>
          <div className="text-2xl font-bold">{priceLabel}</div>
          <div className="mt-1 inline-block rounded-full bg-green-100 px-2.5 py-0.5 text-xs font-medium text-green-800 dark:bg-green-900/30 dark:text-green-300">
            {tierLabel} — PPP discount
          </div>
        </>
      ) : (
        <div className="text-2xl font-bold">{priceLabel}</div>
      )}
    </div>
  );
}

/** Buy the course for yourself or for a team. */
export function EnrollButton({
  course,
  pppPrice,
  tierLabel,
  currentUserId,
}: EnrollButtonProps) {
  const priceLabel = formatPrice(pppPrice);
  const price = {
    fullPrice: course.price,
    priceLabel,
    tierLabel,
    discounted: pppPrice < course.price,
  };

  return (
    <Tabs defaultValue="self">
      <PurchaseTabsList />
      <TabsContentNoShift>
        <TabsContent value="self" forceMount>
          <PriceDisplay {...price} />
          <Link to={purchaseLink(course.slug, currentUserId, false)}>
            <Button size="lg" className="w-full">
              Enroll Now — {priceLabel}
            </Button>
          </Link>
        </TabsContent>
        <TabsContent value="team" forceMount>
          <PriceDisplay {...price} />
          <Link to={purchaseLink(course.slug, currentUserId, true)}>
            <Button size="lg" variant="outline" className="w-full">
              Buy for Your Team
            </Button>
          </Link>
        </TabsContent>
      </TabsContentNoShift>
    </Tabs>
  );
}
