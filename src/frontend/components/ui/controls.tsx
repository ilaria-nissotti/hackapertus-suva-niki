import type {ComponentProps} from 'react';

// Adapted from shadcn/ui New York: native hosts and data slots, styled by the app's CSS.
type ButtonProps = ComponentProps<'button'> & {variant?: 'default' | 'outline' | 'ghost'};

export function Button({variant = 'default', type = 'button', ...props}: ButtonProps) {
  return <button type={type} data-slot="button" data-variant={variant} {...props}/>;
}

export function Input(props: ComponentProps<'input'>) {
  return <input data-slot="input" {...props}/>;
}

export function Textarea(props: ComponentProps<'textarea'>) {
  return <textarea data-slot="textarea" {...props}/>;
}

export function Badge(props: ComponentProps<'span'>) {
  return <span data-slot="badge" {...props}/>;
}

export function Collapsible({label, children, ...props}: ComponentProps<'details'> & {label: string}) {
  return <details data-slot="collapsible" {...props}>
    <summary data-slot="collapsible-trigger">{label}</summary>
    <div data-slot="collapsible-content">{children}</div>
  </details>;
}
