import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Mirrors bizos.util.ids.slugify so previews match FreedomBot workspace IDs. */
export function previewSlug(value: string): string {
  let slug = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 40)
    .replace(/_+$/g, '')
  if (!slug) slug = 'client'
  if (/^[0-9]/.test(slug)) slug = `c_${slug}`
  return slug
}
