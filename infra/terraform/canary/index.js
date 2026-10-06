// External health check for the Investor Dashboard (SI-4, CP-2).
// Runtime: syn-nodejs-puppeteer-17.0 (uses the @aws/* Synthetics namespaces from 13.1+).
// Fetches serving/health.json through CloudFront and fails when:
//   - the page is not HTTP 200 (site or CDN down, sign-in misconfigured)
//   - the data is older than MAX_AGE_MIN during US market hours (pipeline stalled)
//   - any P1 source is stale (a must-have feed has stopped)
// health.json must be exempt from sign-in and hold no sensitive data, e.g.
//   {"generated_at": "2026-10-05T14:05:12Z", "stale_p1": 0, "stale_total": 1}

const synthetics = require('@aws/synthetics-puppeteer');
const log = require('@aws/synthetics-logger');

const healthUrl = new URL(process.env.HEALTH_URL);
const maxAgeMin = Number(process.env.MAX_AGE_MIN || '90');

// Regular-session hours, Mon-Fri 10:00-17:00 ET (data refreshes hourly from 10:05).
// Holidays still alert; mute the alarm on NYSE holidays if that is noisy.
function inMarketHoursET(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    weekday: 'short',
    hour: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(now);
  const weekday = parts.find((p) => p.type === 'weekday').value;
  const hour = Number(parts.find((p) => p.type === 'hour').value);
  return !['Sat', 'Sun'].includes(weekday) && hour >= 10 && hour < 17;
}

function checkHealth(body) {
  const health = JSON.parse(body);
  const generatedAt = Date.parse(health.generated_at);
  if (Number.isNaN(generatedAt)) {
    throw new Error('health.json has no valid generated_at');
  }
  const ageMin = (Date.now() - generatedAt) / 60000;
  log.info(`health.json age ${ageMin.toFixed(1)} min, stale_p1=${health.stale_p1}`);
  if (inMarketHoursET() && ageMin > maxAgeMin) {
    throw new Error(`Data is ${Math.round(ageMin)} min old (limit ${maxAgeMin} min in market hours)`);
  }
  if (Number(health.stale_p1 || 0) > 0) {
    throw new Error(`${health.stale_p1} P1 source(s) are stale`);
  }
}

const validateResponse = (res) =>
  new Promise((resolve, reject) => {
    if (res.statusCode !== 200) {
      reject(new Error(`Expected HTTP 200, got ${res.statusCode}`));
      return;
    }
    let body = '';
    res.on('data', (chunk) => {
      body += chunk;
    });
    res.on('end', () => {
      try {
        checkHealth(body);
        resolve();
      } catch (err) {
        reject(err);
      }
    });
    res.on('error', reject);
  });

exports.handler = async () => {
  synthetics.getConfiguration().setConfig({
    includeRequestHeaders: false,
    includeResponseHeaders: true,
    includeResponseBody: false,
    restrictedHeaders: ['authorization', 'cookie', 'set-cookie'],
  });

  const requestOptions = {
    hostname: healthUrl.hostname,
    method: 'GET',
    path: `${healthUrl.pathname}${healthUrl.search}`,
    port: 443,
    protocol: 'https:',
    headers: {
      'User-Agent': synthetics.getCanaryUserAgentString(),
      'Cache-Control': 'no-cache',
    },
  };

  return synthetics.executeHttpStep('fetch-health-json', requestOptions, validateResponse);
};
