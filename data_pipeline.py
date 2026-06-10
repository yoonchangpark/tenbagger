import logging
import json
from pytrends.request import TrendReq
from yahooquery import search, Ticker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_trending_topics():
    """Fetch trending topics using pytrends."""
    logger.info("Fetching trending topics via Google Trends...")
    try:
        pytrends = TrendReq(hl='en-US', tz=360)
        trending = pytrends.trending_searches(pn='united_states')
        top_trends = trending.head(3)[0].tolist()
        return top_trends
    except Exception as e:
        logger.error(f"Error fetching trends: {e}")
        return ["NVDA Stock", "Tesla EV Market", "Bitcoin ETFs"]

def fetch_facts_for_topic(topic):
    """Fetch news and facts for a given topic using Yahoo Finance."""
    logger.info(f"Fetching facts for topic: {topic}")
    facts = []
    try:
        res = search(topic)
        quotes = res.get('quotes', [])
        
        if quotes:
            symbol = quotes[0]['symbol']
            logger.info(f"Identified ticker {symbol} for topic {topic}")
            ticker = Ticker(symbol)
            news = ticker.news
            
            if isinstance(news, list):
                # Using top 3 news articles to summarize
                for item in news[:3]:
                    title = item.get('title', '')
                    summary = item.get('summary', '') or item.get('content', '')
                    if title:
                        facts.append(f"- {title}: {summary[:200]}...") # Limit summary length
        else:
            logger.info(f"No specific ticker found for {topic}.")
            
    except Exception as e:
        logger.error(f"Error fetching facts: {e}")
        
    if not facts:
         facts.append(f"Topic '{topic}' is currently seeing major market movements. Analysts are closely watching the trend for the upcoming quarter.")

    return "\n".join(facts)

if __name__ == "__main__":
    trends = get_trending_topics()
    print("Trends:", trends)
    if trends:
        facts = fetch_facts_for_topic(trends[0])
        print(f"\nFacts for {trends[0]}:\n{facts}")
