# Explaining Carbon Window

## A two-minute demonstration

1. Start on **Find a window**. Point out the recorded snapshot date. Use one kWh and a
   two-hour activity, then change the finish deadline. Explain why the best start can change.
2. Show **Explore the data**. Compare the time series with the heatmap. An average pattern
   is useful context, but it cannot guarantee tomorrow's cleanest hour.
3. Open **Model experiment**. The time-of-day baseline beats the gradient-boosting model
   on this run. Explain why reporting that is more useful than selecting only a flattering metric.
4. Finish on **Data quality**. Show the interval counts, source hashes and missingness checks.

## Questions to be able to answer

**Why SQLite?** The dataset is small and relational. SQL makes daily aggregation clear,
constraints protect the time keys, and transactions keep a failed refresh from replacing
good data. A distributed database would add little here.

**Why a composite key?** `(run_id, start_utc)` permits the same half-hour in different
snapshots while preventing duplicate observations within a run. Forecasts and estimated
actuals may be revised, so preserving the retrieval context matters.

**How are emissions calculated?** Average forecast intensity in gCO₂/kWh multiplied by
total kWh gives grams of estimated CO₂. Equal consumption in each half-hour is assumed.
A realistic appliance load profile would require a weighted sum instead.

**Why no random split?** A random split lets later conditions influence training used
to predict earlier ones. Here each weekly test follows its training data. The three
folds reveal that model performance changes over time.

**What does MAE mean?** Average absolute error in the original units. RMSE penalises
large misses more strongly; bias shows systematic over- or underprediction.
These scores assess prediction error, not proof of emissions saved.

**Why doesn't the model beat the baseline?** Calendar features cannot describe the
weather, demand or generator availability. More complexity can fit patterns that fail
to persist. The result suggests improving the information and evaluation coverage
before increasing model complexity.

**Is the provider forecast a fair benchmark?** Not as a day-ahead forecast here. The
historical endpoint supplies a forecast value but not its original issue timestamp.
A defensible comparison would archive forecast vintages as they become available and
match horizons exactly.

**What happens when data is missing?** The database stores NULL, aggregates use valid
actuals, the dashboard reports missingness, and incomplete planning windows are excluded.
An absent forecast must never become a zero-carbon opportunity.

## Small changes to practise yourself

- Calculate one two-hour recommendation by hand from four half-hour forecasts.
- Write a SQL query for the five highest-intensity complete days. Explain the denominator.
- Add a test where the cheapest-looking window crosses a missing interval.
- Change the model's leaf count in a separate experiment. Compare all folds, not just the
  best week, and keep the original benchmark intact.
- Explain how you would add a varying appliance load without claiming a constant-power model.

## Sensible next step within this project

Capture forecast vintages at a fixed lead time and evaluate the actual decisions that
would have been possible then. That would improve evidence more than adding another
algorithm or calling the current results a production system.
