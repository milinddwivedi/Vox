const { createClient } = require('@supabase/supabase-js');

const supabaseUrl = process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL || 'https://qiigoipgoedexmdowtvj.supabase.co';
const supabaseServiceKey = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_ANON_KEY || 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFpaWdvaXBnb2VkZXhtZG93dHZqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQ3NTM2NjQsImV4cCI6MjExMDMyOTY2NH0.C9Bz6OYqxB5uBUa0kEBJpiMvwW6SbKYWhAEcVZoFvYM';

const supabase = createClient(supabaseUrl, supabaseServiceKey, {
  auth: {
    persistSession: false,
    autoRefreshToken: false,
  },
});

/**
 * Validates bearer token from Authorization header and returns user profile.
 */
async function authenticateUser(req) {
  const authHeader = req.headers.authorization || req.headers.Authorization;
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return null;
  }
  const token = authHeader.split(' ')[1];
  try {
    const { data: { user }, error } = await supabase.auth.getUser(token);
    if (error || !user) {
      return null;
    }
    return user;
  } catch (err) {
    console.error('Error in authenticateUser:', err);
    return null;
  }
}

module.exports = {
  supabase,
  authenticateUser,
};
