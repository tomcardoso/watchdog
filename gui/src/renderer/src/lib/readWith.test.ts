// Unit tests for the Documents screen's "Text read with" line (D372). Run with `npm run test:unit`.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readWith } from './readWith.ts'

test('says nothing for a document pre-processed before the engine was recorded', () => {
  assert.equal(readWith(null), null)
  assert.equal(readWith({ ocr_used: true }), null)
  assert.equal(readWith({ ocr_engine: 'unknown' }), null)
})

test("names the file's own text when no engine read it", () => {
  assert.equal(readWith({ ocr_engine: 'none' }), "The file's own text")
})

test('names the engine, its version and the pages it read', () => {
  assert.equal(readWith({ ocr_engine: 'ocrmac', ocr_used: true }), 'Apple Vision (OCR on every page)')
  assert.equal(
    readWith({ ocr_engine: 'rapidocr', ocr_engine_version: '3.10.0', ocr_used: true, ocr_pages: [15, 21] }),
    'RapidOCR 3.10.0 (OCR on pages 15, 21)'
  )
  assert.equal(readWith({ ocr_engine: 'tesseract', ocr_used: true, ocr_pages: [3] }), 'Tesseract (OCR on page 3)')
})

test('names every engine when slices differed, without a version', () => {
  assert.equal(
    readWith({ ocr_engine: 'rapidocr+tesseract', ocr_engine_version: '2.12.0', ocr_used: true }),
    'RapidOCR and Tesseract (OCR on every page)'
  )
})

test('says the engine only read images when no page was OCR’d whole', () => {
  assert.equal(readWith({ ocr_engine: 'rapidocr', ocr_used: false }), "The file's own text; RapidOCR for any images")
})
