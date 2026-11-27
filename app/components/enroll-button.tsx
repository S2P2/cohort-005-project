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
  fullPrice: number;
  /** What this Person pays, after any Purchasing-Power discount. */
  pppPrice: number;
  tierLabel: string;
  /** Where "Buy for Myself" and "Buy for Team" lead. */
  purchaseLinks: { self: string; team: string };
};

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
  fullPrice,
  pppPrice,
  tierLabel,
  purchaseLinks,
}: EnrollButtonProps) {
  const priceLabel = formatPrice(pppPrice);
  const price = {
    fullPrice,
    priceLabel,
    tierLabel,
    discounted: pppPrice < fullPrice,
  };

  return (
    <Tabs defaultValue="self">
      <PurchaseTabsList />
      <TabsContentNoShift>
        <TabsContent value="self" forceMount>
          <PriceDisplay {...price} />
          <Link to={purchaseLinks.self}>
            <Button size="lg" className="w-full">
              Enroll Now — {priceLabel}
            </Button>
          </Link>
        </TabsContent>
        <TabsContent value="team" forceMount>
          <PriceDisplay {...price} />
          <Link to={purchaseLinks.team}>
            <Button size="lg" variant="outline" className="w-full">
              Buy for Your Team
            </Button>
          </Link>
        </TabsContent>
      </TabsContentNoShift>
    </Tabs>
  );
}
