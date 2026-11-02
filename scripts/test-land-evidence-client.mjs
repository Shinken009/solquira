// Offline parser invariants. All coordinates and metadata below are synthetic test fixtures.
// Node's built-in TypeScript stripping imports only the pure client parser; no fetch runs.
import test from 'node:test';
import assert from 'node:assert/strict';
import { EVIDENCE_IDS, parseLandEvidence } from '../artifacts/solquira/src/studio/land-evidence-client.ts';

const point = { latitude: 52.520008, longitude: 13.404954 };
const natures = ['forecast', 'reanalysis', 'satellite', 'satellite', 'modeled_soil', 'terrain', 'self_reported'];
function missingInventory(selected = point) {
  return {
    schema_version: 'solquira.land-evidence/1', status: 'inventory_only', scientific_validation: 'not_validated', is_medalus_r: false,
    location: { ...selected }, generated_at: new Date().toISOString(), limitations: ['Synthetic test metadata, not land measurements.'],
    items: EVIDENCE_IDS.map((id, i) => ({
      id, label: 'Synthetic inventory slot', state: 'missing', reason: 'No source included.', nature: natures[i], source: null,
      period: null, retrieved_at: null, spatial_support: null, units: null, coverage: null, quality: 'not_assessed',
      uncertainty: 'Unknown.', limitations: ['No condition inferred.'], next_observation: 'Record only what was observed.',
    })),
  };
}
function forecastInventory(selected = point) {
  const value = missingInventory(selected), now = new Date(value.generated_at);
  const start = now.toISOString().slice(0, 10);
  const end = new Date(Date.parse(`${start}T00:00:00Z`) + 6 * 86400000).toISOString().slice(0, 10);
  Object.assign(value.items[0], {
    state: 'available', reason: 'Included model-forecast metadata, not a measurement.', quality: 'metadata_checked',
    source: { id: 'open-meteo-forecast', name: 'Open-Meteo', version: null, method: 'seven-day-model-forecast', url: 'https://open-meteo.com/en/docs', license_url: 'https://open-meteo.com/en/terms' },
    period: { start, end, time_zone: 'UTC' }, retrieved_at: now.toISOString(), units: ['mm', '°C'],
    spatial_support: { description: 'Unknown grid footprint and resolution.', resolution_m: null, query_precision_degrees: 0.001 },
  });
  return value;
}
const rejected = (value, selected = point) => assert.throws(() => parseLandEvidence(value, selected), /could not be verified/);

test('preserves raw point and nulls without inventing measurements', () => {
  const value = missingInventory();
  assert.equal(parseLandEvidence(value, point), value);
  assert.deepEqual(value.location, point);
  assert(value.items.every(item => item.units === null && item.coverage === null));
});
test('accepts current forecast metadata but keeps climate baseline missing', () => {
  const value = forecastInventory();
  assert.equal(parseLandEvidence(value, point).items[0].state, 'available');
  assert.equal(value.items[1].state, 'missing');
  assert.equal(value.items[0].spatial_support.resolution_m, null);
});
test('keeps explicit stale and unavailable states without making them available', () => {
  for (const state of ['stale', 'unavailable', 'not_applicable']) {
    const value = missingInventory(); value.items[0].state = state;
    assert.equal(parseLandEvidence(value, point).items[0].state, state);
  }
});
test('rejects a different point and non-finite coordinates', () => {
  for (const latitude of [1, NaN, Infinity, 91]) {
    const value = missingInventory(); value.location.latitude = latitude; rejected(value);
  }
});
test('rejects invalid state, duplicate slots and missing slots', () => {
  const unknown = missingInventory(); unknown.items[0].state = 'validated_for_everywhere'; rejected(unknown);
  const duplicate = missingInventory(); duplicate.items[1] = duplicate.items[0]; rejected(duplicate);
  const absent = missingInventory(); absent.items.pop(); rejected(absent);
});
test('rejects changing a slot nature or using forecast as climatology', () => {
  for (const i of [1, 2, 4, 5, 6]) {
    const value = missingInventory(); value.items[i].nature = 'forecast'; rejected(value);
  }
});
test('rejects new available layers and scientific-validation claims', () => {
  const layer = missingInventory(); layer.items[4].state = 'available'; rejected(layer);
  const validated = missingInventory(); validated.scientific_validation = 'validated'; rejected(validated);
  const index = missingInventory(); index.is_medalus_r = true; rejected(index);
});
test('rejects forecast units that could disguise a conversion', () => {
  for (const units of [['cm', '°C'], ['mm'], ['mm', 'K'], ['mm', '°C', '%'], null]) {
    const value = forecastInventory(); value.items[0].units = units; rejected(value);
  }
});
test('requires UTC timestamps, calendar dates and ordered periods', () => {
  for (const generated_at of ['2026-02-30T12:00:00Z', '2026-11-02', '2026-11-02T12:00:00', '2026-11-02T12:00:00+02:00', '0000-01-01T00:00:00Z']) {
    const value = missingInventory(); value.generated_at = generated_at; rejected(value);
  }
  for (const period of [{ start: '2026-02-30', end: '2026-03-01', time_zone: 'UTC' }, { start: '2026-11-02', end: '2026-11-01', time_zone: 'UTC' }, { start: '2026-11-01T00:00:00Z', end: '2026-11-07T00:00:00Z', time_zone: 'UTC' }]) {
    const value = forecastInventory(); value.items[0].period = period; rejected(value);
  }
});
test('does not treat expired, future or wrong-period forecasts as currently available', () => {
  const future = missingInventory(); future.generated_at = new Date(Date.now() + 120000).toISOString(); rejected(future);
  const expired = forecastInventory(); expired.items[0].retrieved_at = new Date(Date.parse(expired.generated_at) - 600000).toISOString(); rejected(expired);
  const fetchedLater = forecastInventory(); fetchedLater.items[0].retrieved_at = new Date(Date.parse(fetchedLater.generated_at) + 120000).toISOString(); rejected(fetchedLater);
  const short = forecastInventory(); short.items[0].period.end = short.items[0].period.start; rejected(short);
  const old = forecastInventory(); old.items[0].period = { start: '2020-01-01', end: '2020-01-07', time_zone: 'UTC' }; rejected(old);
});
test('rejects unsafe or substituted source links', () => {
  for (const url of ['http://open-meteo.com/en/docs', 'javascript:alert(1)', 'https://user:secret@open-meteo.com/en/docs', 'https://untrusted.invalid/']) {
    const value = forecastInventory(); value.items[0].source.url = url; rejected(value);
  }
});
test('validates antimeridian footprints and equivalent +180/-180 boundaries', () => {
  for (const longitude of [175, -175, 180, -180]) {
    const selected = { latitude: 0, longitude }, value = missingInventory(selected);
    value.items[0].coverage = { crs: 'EPSG:4326', bbox: [170, -10, -170, 10] };
    assert.equal(parseLandEvidence(value, selected), value);
  }
  const edge = { latitude: 0, longitude: 180 }, value = missingInventory(edge);
  value.items[0].coverage = { crs: 'EPSG:4326', bbox: [-180, -10, -170, 10] };
  assert.equal(parseLandEvidence(value, edge), value);
  const outside = missingInventory(); outside.items[0].coverage = { crs: 'EPSG:4326', bbox: [170, -10, -170, 10] }; rejected(outside);
});
test('rejects invalid or out-of-footprint coverage and negative source resolution', () => {
  for (const bbox of [[-180, 10, 180, -10], [-181, -90, 180, 90], [0, -90, 1, 90], [NaN, -90, 180, 90]]) {
    const value = missingInventory(); value.items[0].coverage = { crs: 'EPSG:4326', bbox }; rejected(value);
  }
  const value = forecastInventory(); value.items[0].spatial_support.resolution_m = -1; rejected(value);
});
