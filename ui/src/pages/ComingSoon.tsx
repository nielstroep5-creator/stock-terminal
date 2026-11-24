import { House, SearchX } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Card, Page } from '@/components/app/Page'
import { Button } from '@/components/ui/button'

/** An address the app does not know (an old bookmark, a typo). */
export function ComingSoon({ title }: { title: string }) {
  return (
    <Page title={title}>
      <Card className="flex flex-col items-center gap-3 px-6 py-16 text-center">
        <span className="flex size-11 items-center justify-center rounded-full bg-secondary text-muted-foreground">
          <SearchX className="size-5" />
        </span>
        <h2 className="text-base font-semibold">There is no page here</h2>
        <p className="max-w-md text-muted-foreground">Use the menu on the left or search for a company.</p>
        <Button asChild variant="default" className="mt-1">
          <Link to="/">
            <House />
            Home
          </Link>
        </Button>
      </Card>
    </Page>
  )
}
