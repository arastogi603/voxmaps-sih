import test from 'node:test';
import assert from 'node:assert/strict';
import { GRID, makeGrid, makeTimeline, scenarioAt } from '../src/model.js';

test('illustrative forecast keeps a 72-hour timeline and finite grid values', () => {
  const timeline = makeTimeline('compound', true);
  const cells = makeGrid(18, 'compound', true);
  assert.equal(timeline.length, 73);
  assert.equal(cells.length, GRID.columns * GRID.rows);
  assert.ok(cells.every((cell) => Number.isFinite(cell.pm25) && Number.isFinite(cell.aqi)));
});

test('the feedback comparison produces an observable scenario difference', () => {
  const withFeedback = scenarioAt(77.215, 28.63, 18, 'compound', true);
  const withoutFeedback = scenarioAt(77.215, 28.63, 18, 'compound', false);
  assert.notEqual(withFeedback.pm25, withoutFeedback.pm25);
});
