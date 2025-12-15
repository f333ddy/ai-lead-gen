industries = [
    'Affiliates',
    'General-Reseller and Web Reseller',
    'General-Web Order and Small End User',
    'Entertainment-Distributor or Contractor',
    'Entertainment-Amusement Parks',
    'Entertainment-Convention Centers',
    'Entertainment-Museums',
    'Entertainment-Sports Venues and Teams',
    'Entertainment-Movie Theaters and Theaters',
    'Entertainment-Casinos and Gaming',
    'Entertainment-International',
    'Entertainment-Other',
    'Finance-Distributor Contractor',
    'Finance-Banks and Cash Houses',
    'Finance-Utilities Cable and Cellular',
    'Finance-International',
    'Finance-Other',
    'Government-Distributor or Contractor',
    'Government-DHS TSA CATSA',
    'Government-Federal Agency',
    'Government-Military',
    'Government-State and Local Agency',
    'Government-International',
    'Government-Other',
    'Hospitality-Distributor or Contractor',
    'Hospitality-Hotels',
    'Hospitality-Restaurants and Clubs',
    'Hospitality-International',
    'Hospitality-Other',
    'Institutions-Distributor or Contractor',
    'Institutions-Churches and Temples',
    'Institutions-Hospitals and Medical Centers',
    'Institutions-Schools and Colleges',
    'Institutions-International',
    'Institutions-Other',
    'Railings-Dealer or Distributor',
    'Railings-Construction',
    'Railings-Fabricator',
    'Railings-International',
    'Railings-Other',
    'Retail-Distributor or Contractor',
    'Retail-Retail Product Manufacturer',
    'Retail-Drug Store or Pharmacy',
    'Retail-Supermarket or Grocery',
    'Retail-General Merchandise',
    'Retail-Convenience Stores',
    'Retail-Shopping Malls',
    'Retail-International',
    'Retail-Other',
    'Safety-Distributor',
    'Safety-Industrial Manufacturers',
    'Safety-International',
    'Safety-Other',
    'Transportation-Distributor or Specifier',
    'Transportation-Airlines',
    'Transportation-Airports and Terminals',
    'Transportation-Car Rental',
    'Transportation-Construction Company',
    'Transportation-Land and Water',
    'Transportation-International',
    'Transportation-Other',
    'Architect / Specifier',
    'Entertainment',
    'Government',
    'Hospitality',
    'Healthcare',
    'Railings / Systems and Components',
    'Retail',
    'Transportation',
]

mock_articles = [
    {
        "title": "City Approves $18M Terminal Expansion at Westfield International Airport",
        "link": "https://news.example.com/westfield-airport-terminal-expansion",
        "description": """
        <p>The Westfield City Council <strong>approved an $18 million capital appropriation</strong> 
        for a major expansion of the main terminal at <strong>Westfield International Airport</strong>.</p>
        <p>The project, scheduled for <strong>FY2026</strong>, includes expanded security screening areas, 
        upgraded <em>queuing and crowd control</em> lanes, new <em>public guidance</em> signage, and 
        additional boarding gates.</p>
        <p>The airport falls under the <strong>Transportation-Airports and Terminals</strong> industry.</p>
        """
    },
    {
        "title": "Regional Hospital Announces Lobby Renovation and Wayfinding Upgrade",
        "link": "https://news.example.com/regional-hospital-lobby-renovation",
        "description": """
        <p>Northview Regional Hospital, classified under 
        <strong>Institutions-Hospitals and Medical Centers</strong>, announced a 
        <strong>lobby renovation project</strong> slated for <strong>2025</strong>.</p>
        <p>The project includes new <em>railing systems</em>, improved <em>public guidance</em> and 
        <em>signage</em>, and expanded <em>queuing &amp; crowd control</em> areas for check-in.</p>
        <p>Total construction budget is estimated at <strong>$4.5 million</strong>, funded from the 
        hospital's capital improvement plan.</p>
        """
    },
    {
        "title": "Riverside Casino Invests in Guest Flow and Security Upgrades",
        "link": "https://news.example.com/riverside-casino-guest-flow-upgrades",
        "description": """
        <p>Riverside Casino &amp; Resort, an 
        <strong>Entertainment-Casinos and Gaming</strong> facility, announced a 
        <strong>$2.2 million renovation</strong> focused on guest circulation and security.</p>
        <ul>
            <li>New <em>queuing &amp; crowd control</em> systems at gaming floor entrances</li>
            <li>Permanent <em>post &amp; panel barriers</em> for restricted areas</li>
            <li>Updated <em>signage</em> for wayfinding and capacity messaging</li>
        </ul>
        <p>The upgrades are planned for <strong>FY2025</strong> and will be executed in phases.</p>
        """
    },
    {
        "title": "First Horizon Bank Secures $5M for Core Banking Software Modernization",
        "link": "https://news.example.com/first-horizon-banking-software-grant",
        "description": """
        <p>First Horizon Bank, in the <strong>Finance-Banks and Cash Houses</strong> industry, 
        announced it has received a <strong>$5 million technology grant</strong> to modernize 
        its <strong>core banking software systems</strong>.</p>
        <p>The funds are earmarked exclusively for <em>software services, cloud infrastructure, and 
        cybersecurity tools</em>. No facility renovations, <em>public guidance</em>, 
        <em>queuing &amp; crowd control</em>, <em>railing systems</em>, <em>signage</em>, 
        <em>post &amp; panel barriers</em>, or <em>store fixtures</em> are included in the scope.</p>
        """
    },
    {
        "title": "National Grocery Chain Launches Store Remodel Program Focused on Fixtures and Queues",
        "link": "https://news.example.com/national-grocery-remodel-program",
        "description": """
        <p>FreshMart, a <strong>Retail-Supermarket or Grocery</strong> chain, announced a 
        multi-year <strong>store remodel program</strong> beginning in <strong>2025</strong>.</p>
        <p>The program covers new <em>store fixtures</em>, updated <em>signage</em>, and redesigned 
        <em>queuing &amp; crowd control</em> layouts at front-end checkouts.</p>
        <p>Phase one is budgeted at approximately <strong>$12 million</strong>, with additional 
        phases anticipated in subsequent fiscal years.</p>
        """
    },
    {
        "title": "City Issues RFP for Stadium Crowd Control Barriers and Signage",
        "link": "https://news.example.com/stadium-crowd-control-rfp",
        "description": """
        <p>The City of Lakeside, classified under <strong>Government-State and Local Agency</strong>, 
        has issued an <strong>RFP</strong> for a local sports stadium operated with 
        <strong>Entertainment-Sports Venues and Teams</strong>.</p>
        <p>The solicitation requests proposals for:</p>
        <ul>
            <li>Permanent and portable <em>queuing &amp; crowd control</em> systems</li>
            <li><em>Post &amp; panel barriers</em> for restricted sections</li>
            <li>Wayfinding and safety <em>signage</em> for entry plazas and concourses</li>
        </ul>
        <p>Responses are due by <strong>2025-09-30</strong>, with an estimated budget of 
        <strong>$1.3 million</strong>.</p>
        """
    },
    {
        "title": "Regional Utility Wins Grant for Cybersecurity and Grid Monitoring Software",
        "link": "https://news.example.com/utility-cybersecurity-grant",
        "description": """
        <p>TriCounty Energy, operating in the 
        <strong>Finance-Utilities Cable and Cellular</strong> segment, has secured a 
        <strong>$9 million federal grant</strong> for <strong>cybersecurity and grid monitoring software</strong>.</p>
        <p>The funding is explicitly limited to <em>software licenses, monitoring tools, and consultant services</em>.
        It does not include any physical facility remodels, <em>public guidance</em>, 
        <em>queuing &amp; crowd control</em>, <em>railing systems</em>, <em>signage</em>, 
        <em>post &amp; panel barriers</em>, or <em>store fixtures</em>.</p>
        """
    },
    {
        "title": "Airport Renews SaaS Agreement for Passenger Analytics Platform",
        "link": "https://news.example.com/airport-saas-passenger-analytics",
        "description": """
        <p>SkyHarbor Airport, in the <strong>Transportation-Airports and Terminals</strong> industry, 
        has renewed a three-year <strong>SaaS contract</strong> for its passenger analytics and 
        forecasting platform.</p>
        <p>The contract covers only <em>software services and data integrations</em>. 
        No funding is allocated for <em>public guidance</em> systems, 
        <em>queuing &amp; crowd control</em> hardware, <em>railing systems</em>, 
        <em>signage</em>, <em>post &amp; panel barriers</em>, or <em>store fixtures</em>.</p>
        """
    },
    {
        "title": "State University Approves Capital Plan Including New Student Center",
        "link": "https://news.example.com/state-university-student-center-capital-plan",
        "description": """
        <p>The Board of Trustees at Stateview University, an 
        <strong>Institutions-Schools and Colleges</strong> organization, approved a 
        <strong>$65 million capital plan</strong> for <strong>FY2027</strong>.</p>
        <p>A major component is a new student center that will feature redesigned 
        entry <em>queuing &amp; crowd control</em> areas, integrated <em>public guidance</em> 
        and <em>signage</em>, and interior <em>railing systems</em> for multi-level spaces.</p>
        <p>Separate parts of the plan fund academic software and IT upgrades, but the student center 
        scope specifically calls for physical <em>Public Guidance</em>, 
        <em>Queuing &amp; Crowd Control</em>, <em>Railing Systems</em>, and <em>Signage</em>.</p>
        """
    },
    {
        "title": "Local Restaurant Group Celebrates 10 Years with Customer Story Campaign",
        "link": "https://news.example.com/restaurant-group-customer-stories",
        "description": """
        <p>BrightBites Hospitality, a <strong>Hospitality-Restaurants and Clubs</strong> operator, 
        launched a marketing campaign highlighting customer stories to celebrate its 10th anniversary.</p>
        <p>The announcement focuses on community engagement and brand storytelling. 
        It does <strong>not</strong> mention any new funding, capital appropriations, 
        facility renovations, <em>public guidance</em>, <em>queuing &amp; crowd control</em>, 
        <em>railing systems</em>, <em>signage</em>, <em>post &amp; panel barriers</em>, 
        <em>store fixtures</em>, or procurement activity.</p>
        """
    },
]
    
data = [
{
    "title": "New allocation of City Council for rennovations",
    "category": {"type": "news"},
    "description": "<p>City Council approved a $4.2M allocation to the Facilities Department for lobby <b>renovations</b> in FY2026.</p>",
    "link": "https://example.org/council-minutes"
},
{
    "title": "Community events this summer!",
    "category": {"type": "blog"},
    "description": "<p>We love community events every summer!</p>",
    "link": "https://example.org/community-events"
}
]
